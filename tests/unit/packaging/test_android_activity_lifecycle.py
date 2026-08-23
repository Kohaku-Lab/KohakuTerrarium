"""Regression contracts for the packaged Android Activity lifecycle."""

from pathlib import Path

_ACTIVITY = (
    Path(__file__).resolve().parents[3]
    / "packaging"
    / "android"
    / "template"
    / "app"
    / "src"
    / "main"
    / "java"
    / "org"
    / "kohaku"
    / "terrarium"
    / "MainActivity.java"
)


class TestAndroidActivityLifecycle:
    def test_destroyed_activity_rejects_delayed_probe_callbacks(self) -> None:
        source = _ACTIVITY.read_text(encoding="utf-8")
        on_destroy = source.split("protected void onDestroy()", 1)[1].split(
            "private void setupUi()", 1
        )[0]
        load_frontend = source.split("private void loadFrontend(int port)", 1)[1].split(
            "private void handleConnectIntent", 1
        )[0]

        assert on_destroy.index("destroyed = true") < on_destroy.index(
            "webView.destroy()"
        )
        assert "probeHandler.removeCallbacksAndMessages(null)" in on_destroy
        assert "mainHandler.removeCallbacksAndMessages(null)" in on_destroy
        assert "destroyed || isDestroyed() || webView == null" in load_frontend
        assert "if (destroyed) return" in source
