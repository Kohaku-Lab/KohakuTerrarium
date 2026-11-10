//! Desktop coordinates accepted by each platform's native window API.

use tauri::{LogicalPosition, LogicalSize, PhysicalPosition, PhysicalSize, Position, Size};

use crate::overlay::Rect;

#[derive(Clone, Copy)]
pub(crate) enum DesktopSpace {
    Logical,
    Physical,
}

impl DesktopSpace {
    pub const CURRENT: Self = if cfg!(any(target_os = "macos", target_os = "linux")) {
        Self::Logical
    } else {
        Self::Physical
    };

    pub fn divisor(self, scale: f64) -> f64 {
        match self {
            Self::Logical => scale,
            Self::Physical => 1.0,
        }
    }

    pub fn rect(self, physical: Rect, scale: f64) -> Rect {
        let divisor = self.divisor(scale);
        Rect {
            x: physical.x / divisor,
            y: physical.y / divisor,
            w: physical.w / divisor,
            h: physical.h / divisor,
        }
    }

    pub fn point(self, origin: (f64, f64), css: (f64, f64), scale: f64) -> (f64, f64) {
        let divisor = self.divisor(scale);
        (
            (origin.0 + css.0 * scale) / divisor,
            (origin.1 + css.1 * scale) / divisor,
        )
    }

    pub fn position(self, x: f64, y: f64) -> Position {
        match self {
            Self::Logical => LogicalPosition::new(x, y).into(),
            Self::Physical => PhysicalPosition::new(x as i32, y as i32).into(),
        }
    }

    pub fn size(self, width: f64, height: f64) -> Size {
        match self {
            Self::Logical => LogicalSize::new(width, height).into(),
            Self::Physical => PhysicalSize::new(width.round() as u32, height.round() as u32).into(),
        }
    }
}

pub(crate) struct PointerCoordinates {
    pub space: DesktopSpace,
    window_divisor: f64,
    cursor_divisor: f64,
}

impl PointerCoordinates {
    pub fn new(space: DesktopSpace, cursor_scale: f64, window_scale: f64) -> Self {
        Self {
            space,
            window_divisor: space.divisor(window_scale),
            cursor_divisor: space.divisor(cursor_scale),
        }
    }

    pub fn cursor(&self, point: (f64, f64)) -> (f64, f64) {
        (point.0 / self.cursor_divisor, point.1 / self.cursor_divisor)
    }

    pub fn origin(&self, point: (f64, f64)) -> (f64, f64) {
        (point.0 / self.window_divisor, point.1 / self.window_divisor)
    }

    pub fn css_scale(&self, scale: (f64, f64)) -> (f64, f64) {
        (scale.0 / self.window_divisor, scale.1 / self.window_divisor)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pointer_capture_and_origins_share_desktop_space_across_mixed_scale_monitors() {
        for (space, cursor_scale, window_scale, cursor, origin, expected) in [
            (
                DesktopSpace::Logical,
                2.0,
                1.0,
                (3440.0, 240.0),
                (1700.0, 100.0),
                (20.0, 20.0),
            ),
            (
                DesktopSpace::Logical,
                1.0,
                2.0,
                (-1580.0, 120.0),
                (-3200.0, 200.0),
                (20.0, 20.0),
            ),
            (
                DesktopSpace::Physical,
                1.0,
                2.0,
                (3440.0, 240.0),
                (3400.0, 200.0),
                (20.0, 20.0),
            ),
        ] {
            let coordinates = PointerCoordinates::new(space, cursor_scale, window_scale);
            let cursor = coordinates.cursor(cursor);
            let origin = coordinates.origin(origin);
            let scale = coordinates.css_scale((window_scale, window_scale));
            assert_eq!(
                (
                    (cursor.0 - origin.0) / scale.0,
                    (cursor.1 - origin.1) / scale.1
                ),
                expected
            );
        }
    }

    #[test]
    fn cocoa_and_gtk_restore_points_independently_of_the_current_monitor_scale() {
        for current_scale in [1.0, 2.0] {
            let point = DesktopSpace::Logical
                .position(-1600.0, 120.0)
                .to_logical::<f64>(current_scale);
            let size = DesktopSpace::Logical
                .size(600.0, 800.0)
                .to_logical::<f64>(current_scale);
            assert_eq!((point.x, point.y), (-1600.0, 120.0));
            assert_eq!((size.width, size.height), (600.0, 800.0));
        }
    }

    #[test]
    fn windows_restores_physical_desktop_pixels() {
        let position = DesktopSpace::Physical
            .position(2200.0, 100.0)
            .to_physical::<i32>(2.0);
        let size = DesktopSpace::Physical
            .size(600.0, 800.0)
            .to_physical::<u32>(2.0);
        assert_eq!((position.x, position.y), (2200, 100));
        assert_eq!((size.width, size.height), (600, 800));
    }
}
