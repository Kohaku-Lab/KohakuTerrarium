/*
 * KohakuTerrarium Android — MainActivity (UI only).
 *
 * Replaces Briefcase Android's default MainActivity.  Sole job:
 * show the WebView pointed at the loopback host that
 * KohakuHostService maintains.  Does NOT boot Python — that's
 * the service's job (see the lifecycle docstring on
 * ``KohakuHostService``).
 *
 * Flow:
 *
 *   1. onCreate
 *     - Build the UI (status text + WebView)
 *     - ``startForegroundService(KohakuHostService)`` — kicks off
 *       Python boot + notification on the service side
 *     - Spin up a probe thread that polls the service's port
 *       file + hits ``/healthz``; once 200, ``loadFrontend(port)``
 *
 *   2. onNewIntent — handles ``ktconnect://`` deep links (the
 *      activity is ``singleTop`` per the manifest so re-entries
 *      hit here instead of stacking).
 *
 *   3. onDestroy — tears down WebView + probe thread.  Does NOT
 *      shut Python down; the host keeps running so the
 *      foreground notification accurately reflects "host live".
 *      The host stops only when the user actively swipes the app
 *      from Recents (KohakuHostService.onTaskRemoved).
 *
 * Deep-link ack: ``window.KohakuBridge.ackConnectUri()`` from JS
 * stops the URI replay loop.  Vue's ``useConnectIntent.consume()``
 * calls the bridge as part of the consume step.
 */
package org.kohaku.terrarium;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.file.Files;

public class MainActivity extends Activity {
    private static final String TAG = "KtMainActivity";
    private static final int HOST_BOOT_POLL_MS = 250;
    private static final int HOST_BOOT_TIMEOUT_MS = 30_000;

    private static final int CONNECT_URI_REPLAY_INTERVAL_MS = 1500;
    private static final int CONNECT_URI_REPLAY_MAX_TRIES = 30;  // ~45s ceiling

    private WebView webView;
    private TextView statusView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private HandlerThread probeThread;
    private Handler probeHandler;
    private File configDir;
    private File portFile;
    private String pendingConnectUri;
    private int connectUriReplayCount = 0;
    private boolean webViewLoaded = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        configDir = new File(getFilesDir(), ".kohakuterrarium");
        configDir.mkdirs();
        portFile = new File(configDir, KohakuHostService.PORT_FILENAME);

        setupUi();
        startHostService();

        probeThread = new HandlerThread("kt-host-probe");
        probeThread.start();
        probeHandler = new Handler(probeThread.getLooper());
        pollForHostReady();

        handleConnectIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleConnectIntent(intent);
    }

    @Override
    protected void onDestroy() {
        if (probeThread != null) {
            probeThread.quitSafely();
            probeThread = null;
            probeHandler = null;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    private void setupUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        statusView = new TextView(this);
        statusView.setText("Starting KohakuTerrarium host…");
        statusView.setPadding(48, 96, 48, 16);
        root.addView(statusView);

        webView = new WebView(this);
        webView.setVisibility(View.GONE);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                webViewLoaded = true;
                replayConnectUri();
            }
        });
        webView.addJavascriptInterface(new JsBridge(), "KohakuBridge");

        LinearLayout.LayoutParams webParams = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1.0f
        );
        root.addView(webView, webParams);
        setContentView(root);
    }

    private void startHostService() {
        Intent serviceIntent = new Intent(this, KohakuHostService.class);
        // minSdk = 26, so startForegroundService is available.
        startForegroundService(serviceIntent);
    }

    private void pollForHostReady() {
        final long deadline = System.currentTimeMillis() + HOST_BOOT_TIMEOUT_MS;
        Runnable check = new Runnable() {
            @Override
            public void run() {
                int port = readBoundPort();
                if (port > 0 && probeHost(port)) {
                    final int boundPort = port;
                    mainHandler.post(() -> loadFrontend(boundPort));
                    return;
                }
                if (System.currentTimeMillis() > deadline) {
                    mainHandler.post(() -> statusView.setText(
                        "Host failed to start within 30s.  Check the log."));
                    return;
                }
                probeHandler.postDelayed(this, HOST_BOOT_POLL_MS);
            }
        };
        probeHandler.postDelayed(check, HOST_BOOT_POLL_MS);
    }

    private int readBoundPort() {
        if (portFile == null || !portFile.exists()) return 0;
        try {
            String contents = new String(
                Files.readAllBytes(portFile.toPath())
            ).trim();
            return Integer.parseInt(contents);
        } catch (IOException | NumberFormatException e) {
            return 0;
        }
    }

    private boolean probeHost(int port) {
        try {
            URL url = new URL("http://127.0.0.1:" + port + "/healthz");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(500);
            conn.setReadTimeout(1000);
            try {
                return conn.getResponseCode() == 200;
            } finally {
                conn.disconnect();
            }
        } catch (Exception e) {
            return false;
        }
    }

    private void loadFrontend(int port) {
        statusView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl("http://127.0.0.1:" + port + "/");
    }

    private void handleConnectIntent(Intent intent) {
        if (intent == null) return;
        Uri data = intent.getData();
        if (data == null) return;
        if (!"ktconnect".equalsIgnoreCase(data.getScheme())) return;
        pendingConnectUri = data.toString();
        connectUriReplayCount = 0;  // fresh URI → reset retry counter
        replayConnectUri();
    }

    private void replayConnectUri() {
        if (pendingConnectUri == null || webView == null) return;
        if (!webViewLoaded) return;
        if (connectUriReplayCount >= CONNECT_URI_REPLAY_MAX_TRIES) {
            // Capped retries — Vue side never acked.  Most likely
            // the user navigated away from the host-picker route
            // before consuming the URI.  Drop it instead of
            // dispatching forever.
            Log.w(TAG, "connect URI replay timed out after "
                + CONNECT_URI_REPLAY_MAX_TRIES + " tries; dropping");
            pendingConnectUri = null;
            return;
        }
        connectUriReplayCount += 1;
        String js =
            "(function(){" +
            "  window.__KT_PENDING_CONNECT_URI = " +
            jsString(pendingConnectUri) +
            ";" +
            "  window.dispatchEvent(new Event('kt-connect-uri'));" +
            "})();";
        webView.evaluateJavascript(js, null);
        // Vue's host-picker listener may not be mounted yet;
        // keep replaying (capped) until JS calls
        // KohakuBridge.ackConnectUri() or we hit the ceiling.
        mainHandler.postDelayed(
            this::replayConnectUri, CONNECT_URI_REPLAY_INTERVAL_MS
        );
    }

    private static String jsString(String value) {
        return "\"" + value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r") + "\"";
    }

    private final class JsBridge {
        @JavascriptInterface
        public void ackConnectUri() {
            mainHandler.post(() -> {
                pendingConnectUri = null;
                Log.i(TAG, "JS acked connect URI; stopping replay");
            });
        }
    }
}
