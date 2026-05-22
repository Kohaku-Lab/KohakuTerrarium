/*
 * KohakuTerrarium Android — persistent foreground service.
 *
 * Owns the embedded Python host's lifecycle.  Earlier design split
 * Python boot into MainActivity + notification into Service; that
 * was wrong because:
 *
 *   - When the user backgrounds the app, the Activity can be
 *     destroyed but the Service stays — under the old split,
 *     Python's MainActivity-owned thread died with the Activity,
 *     leaving a notification with no host.
 *   - When the OS kills the Service for memory and ``START_STICKY``
 *     restarts it, the new Service has no Activity context to
 *     boot Python from.
 *
 * The correct split: **Service owns Python**.  The Activity is
 * just a WebView UI that polls the port file the Service's
 * Python writes.  Activity death is harmless; Service is killed
 * only when the user actively swipes the app away
 * (``onTaskRemoved``) or the system reclaims memory.
 *
 * Lifecycle:
 *
 *   Service.onCreate
 *     - createNotificationChannel
 *     - startForeground(notification)
 *     - extractSandbox()
 *     - bootPython() on a worker thread (Chaquopy)
 *     - Python writes port.txt → Activity polls + loads WebView
 *
 *   Service.onTaskRemoved (user swipe)
 *     - requestPythonShutdown() — touches <configDir>/shutdown
 *     - stopSelf()
 *
 *   Service.onDestroy
 *     - requestPythonShutdown() (idempotent)
 *     - shutdown notification (foreground stop)
 *
 *   System-killed + restarted via START_STICKY
 *     - fresh onCreate → fresh Python boot (Python.isStarted() is
 *       process-scoped; the new process has a fresh interpreter)
 */
package org.kohaku.terrarium;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.IBinder;
import android.system.Os;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

public class KohakuHostService extends Service {
    private static final String TAG = "KtHostService";
    private static final String CHANNEL_ID = "kt_host";
    private static final int NOTIFICATION_ID = 1;
    static final String PORT_FILENAME = "port.txt";
    static final String SHUTDOWN_FILENAME = "shutdown";

    private File configDir;
    private File portFile;
    private File shutdownFile;
    private Thread pythonThread;

    // Class-level: tracks whether the Python launcher is currently
    // running on ANY service instance in this process.  Survives
    // service object recreation (a START_STICKY restart in the
    // *same* process — rare but legal), unlike an instance field.
    // Set ``true`` before ``callAttr("main")``, cleared in the
    // worker's ``finally`` so a fresh service restart after a
    // graceful Python exit can boot a new interpreter cycle.
    private static volatile boolean LAUNCHER_RUNNING = false;

    @Override
    public IBinder onBind(Intent intent) {
        return null;  // not a bound service; clients use file polling
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        // startForeground must run within 5s of startForegroundService
        // or the system ANRs us.  Do the cheap, deterministic work
        // first (notification + path setup); push extraction +
        // Python boot off-thread so a slow eMMC or large APK
        // doesn't blow the deadline.
        startForeground(NOTIFICATION_ID, buildNotification());
        setupPaths();
        // Extraction and Python boot share the worker thread so
        // they sequence correctly (Python's env must point at the
        // already-extracted bin dir).
        bootPython();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // User swiped the app away from Recents — treat this as
        // "fully shut down, don't auto-restart."  ``stopSelf``
        // drives the service into ``onDestroy``; without this
        // the foreground notification would linger after the user
        // clearly meant to quit.
        Log.i(TAG, "task removed; shutting host down");
        requestPythonShutdown();
        stopSelf();
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        requestPythonShutdown();
        if (pythonThread != null) {
            // Best-effort join — Python's uvicorn loop sees the
            // shutdown marker and drains; this gives it a moment
            // to do so before the process exits.
            try {
                pythonThread.join(2000);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        super.onDestroy();
    }

    private void setupPaths() {
        configDir = new File(getFilesDir(), ".kohakuterrarium");
        configDir.mkdirs();
        portFile = new File(configDir, PORT_FILENAME);
        shutdownFile = new File(configDir, SHUTDOWN_FILENAME);
        // Audit fix #3: clear BOTH markers on fresh boot.  Stale
        // ``shutdown`` from a previous run would otherwise make
        // the newly-booted Python launcher self-terminate
        // immediately after writing the port file.
        if (portFile.exists()) portFile.delete();
        if (shutdownFile.exists()) shutdownFile.delete();
    }

    private void extractSandbox() throws IOException {
        File binDir = new File(configDir, "bin");
        binDir.mkdirs();

        AssetManager am = getAssets();
        String[] available = am.list("sandbox/bin");
        if (available == null || available.length == 0) {
            Log.w(TAG, "no sandbox assets bundled in APK");
            return;
        }
        String chosenAbi = pickSupportedAbi(available);
        if (chosenAbi == null) {
            Log.w(TAG, "no bundled ABI matches device: " +
                joinSimple(Build.SUPPORTED_ABIS));
            return;
        }
        String[] binaries = am.list("sandbox/bin/" + chosenAbi);
        if (binaries == null) return;
        for (String name : binaries) {
            File target = new File(binDir, name);
            if (target.exists() && target.length() > 0 && target.canExecute()) {
                continue;
            }
            try (
                InputStream in = am.open("sandbox/bin/" + chosenAbi + "/" + name);
                OutputStream out = new FileOutputStream(target)
            ) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
            }
            if (!target.setExecutable(true, false)) {
                Log.w(TAG, "setExecutable failed for " + target);
            }
        }
    }

    private String pickSupportedAbi(String[] bundledAbis) {
        for (String deviceAbi : Build.SUPPORTED_ABIS) {
            for (String bundled : bundledAbis) {
                if (deviceAbi.equals(bundled)) return bundled;
            }
        }
        return null;
    }

    /** Comma-joiner that doesn't require API 26's ``String.join``. */
    private static String joinSimple(String[] parts) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            if (i > 0) sb.append(',');
            sb.append(parts[i]);
        }
        return sb.toString();
    }

    /**
     * Boot CPython via Chaquopy on a worker thread, then call
     * ``kohakuterrarium.launcher.android.main()`` which binds
     * uvicorn + writes the port file.
     */
    private void bootPython() {
        // Race-free guard: set the flag INSIDE the worker thread,
        // just before the work, so a Chaquopy startup throw still
        // pairs correctly with the ``finally`` clear.  An earlier
        // version set the flag in the caller thread before spawning
        // — that left ``LAUNCHER_RUNNING = true`` permanently
        // stuck if ``Python.start()`` threw before the try/finally
        // entered.
        if (LAUNCHER_RUNNING) {
            Log.i(TAG, "Python launcher already running; skipping");
            return;
        }

        pythonThread = new Thread(() -> {
            // Atomic check-and-set inside the worker — if two
            // service instances race to boot in the same process
            // (extremely rare), the second loses cleanly.
            synchronized (KohakuHostService.class) {
                if (LAUNCHER_RUNNING) {
                    Log.i(TAG, "launcher won race; second worker exiting");
                    return;
                }
                LAUNCHER_RUNNING = true;
            }
            try {
                // Extract bundled sandbox before Python sees the
                // env var pointing at it — Python may try to use
                // ``busybox`` on the first bash-tool call.
                try {
                    extractSandbox();
                } catch (IOException e) {
                    Log.e(TAG, "sandbox extraction failed; bash tool unavailable", e);
                }
                try {
                    Os.setenv("KT_PROFILE", "mobile", true);
                    Os.setenv("KT_CONFIG_DIR", configDir.getAbsolutePath(), true);
                    Os.setenv(
                        "KT_SANDBOX_BIN_DIR",
                        new File(configDir, "bin").getAbsolutePath(),
                        true
                    );
                    Os.setenv("KT_PORT_FILE", portFile.getAbsolutePath(), true);
                    Os.setenv("KT_SERVE_PORT", "8001", true);
                } catch (Throwable t) {
                    Log.e(TAG, "Os.setenv failed", t);
                }
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplication()));
                }
                Python py = Python.getInstance();
                py.getModule("kohakuterrarium.launcher.android")
                    .callAttr("main");
            } catch (Throwable t) {
                Log.e(TAG, "Python host crashed", t);
            } finally {
                LAUNCHER_RUNNING = false;
            }
        }, "kt-python-host");
        pythonThread.setDaemon(false);
        pythonThread.start();
    }

    private void requestPythonShutdown() {
        if (shutdownFile == null) return;
        try {
            if (!shutdownFile.createNewFile()) {
                shutdownFile.setLastModified(System.currentTimeMillis());
            }
        } catch (IOException e) {
            Log.w(TAG, "could not write shutdown marker", e);
        }
    }

    private void createNotificationChannel() {
        // minSdk = 26, so we always have NotificationChannel.
        NotificationManager nm =
            (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
            NotificationChannel chan = new NotificationChannel(
                CHANNEL_ID,
                "KohakuTerrarium host",
                NotificationManager.IMPORTANCE_LOW
            );
            chan.setDescription("Keeps the agent host running in the background");
            nm.createNotificationChannel(chan);
        }
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("KohakuTerrarium")
            .setContentText("Host running.  Tap to open.")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pending)
            .setOngoing(true)
            .build();
    }
}
