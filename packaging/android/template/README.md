# Briefcase Android template overrides

Briefcase Android does NOT have a native "merge a tree of files
into the generated project" key in ``pyproject.toml``.  Instead
this directory holds source files that
``packaging/android/postcreate.py`` copies into the
Briefcase-generated tree between ``briefcase create android`` and
``briefcase update android``.

## What we drop in

- ``app/src/main/java/org/kohaku/terrarium/MainActivity.java`` —
  UI-only activity.  WebView host that polls
  ``<configDir>/port.txt`` for the bound port the host service
  writes, then loads ``http://127.0.0.1:<port>/``.  Handles
  ``ktconnect://`` deep links via ``onNewIntent`` (the manifest
  declares ``android:launchMode="singleTop"`` to keep deep-link
  re-entries on the same Activity instance).

- ``app/src/main/java/org/kohaku/terrarium/KohakuHostService.java`` —
  foreground service that owns Python's lifecycle.  Extracts the
  bundled sandbox (``assets/sandbox/bin/<abi>/busybox`` →
  ``<configDir>/bin/busybox``), starts Chaquopy, runs
  ``kohakuterrarium.launcher.android.main()``.  The service
  outlives the activity (the activity is just chrome); when the
  user swipes the app from Recents, ``onTaskRemoved`` writes a
  shutdown marker that drains uvicorn cleanly.

There's no ``PythonHost.java`` anymore — an earlier iteration
used reflection to discover a JNI bootstrap class.  Chaquopy
provides the JNI bootstrap directly via
``com.chaquo.python.Python``, so the indirection went away.

## What we inject via Briefcase's cookiecutter vars (in `pyproject.toml`)

These come through Briefcase's documented
``android_manifest_*_extra_content`` knobs and don't need
``postcreate.py``:

- ``<uses-permission>`` lines (FOREGROUND_SERVICE,
  FOREGROUND_SERVICE_DATA_SYNC, POST_NOTIFICATIONS, CAMERA)
- ``<service>`` declaration for ``KohakuHostService``
- ``<intent-filter>`` for ``ktconnect://`` on the launcher
  activity
- ``android:launchMode="singleTop"`` on the activity
- ``android:usesCleartextTraffic="true"`` on ``<application>``

## What `postcreate.py` does (because Briefcase has no knob for it)

- Copies the two ``.java`` files above into the generated tree
- Mounts the busybox bin tree (from ``packaging/android/bin/``)
  under ``app/src/main/assets/sandbox/bin/``
- Patches the generated manifest's launcher
  ``android:name=".MainActivity"`` to point at our package
- Flips ``android:allowBackup`` from Briefcase's default
  ``true`` → ``false`` (agent state may contain auth tokens that
  shouldn't roundtrip through Google Drive backup; the audit
  caught the duplicate-attribute clash that prevented injecting
  this via extras)
- Removes Briefcase's default
  ``org/beeware/android/MainActivity.java`` so it doesn't ship
  alongside ours

## Running the template locally

After ``briefcase create android`` writes
``build/kohakuterrarium/android/gradle/app/``, run::

    python packaging/android/postcreate.py

then continue with ``briefcase update android`` + ``briefcase
build android``.  The CI workflow runs this sequence
automatically.
