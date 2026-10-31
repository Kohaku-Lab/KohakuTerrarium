//! The native window frame in the page's colours.

use serde_json::Value;
use tauri::WebviewWindow;

use crate::commands::Args;

/// Red, green and blue of a colour.
pub type Rgb = [u8; 3];

/// The caption colour the page asked for: `[r, g, b]` with each part 0 to 255.
pub fn background_of(spec: &Value) -> Option<Rgb> {
    let parts = spec.get("background")?.as_array()?;
    if parts.len() != 3 {
        return None;
    }
    let mut rgb = [0u8; 3];
    for (slot, part) in rgb.iter_mut().zip(parts) {
        let number = part.as_f64()?;
        if !(0.0..=255.0).contains(&number) {
            return None;
        }
        *slot = number.round() as u8;
    }
    Some(rgb)
}

/// Black or white, whichever reads better on `background` (perceived brightness).
#[cfg(any(windows, test))]
pub fn text_for(background: Rgb) -> Rgb {
    let [r, g, b] = background.map(f64::from);
    let brightness = 0.299 * r + 0.587 * g + 0.114 * b;
    if brightness > 150.0 {
        [0, 0, 0]
    } else {
        [255, 255, 255]
    }
}

/// A Windows `COLORREF`: `0x00BBGGRR`.
#[cfg(any(windows, test))]
pub fn colorref(rgb: Rgb) -> u32 {
    u32::from(rgb[0]) | (u32::from(rgb[1]) << 8) | (u32::from(rgb[2]) << 16)
}

#[cfg(windows)]
fn paint(window: &WebviewWindow, background: Rgb) -> bool {
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR,
    };

    let Ok(hwnd) = window.hwnd() else {
        return false;
    };
    let set = |attribute: i32, color: Rgb| {
        let value = colorref(color);
        // SAFETY: `hwnd` is this window's handle and `value` is a 4-byte COLORREF that
        // outlives the call; DWM copies it.
        unsafe {
            DwmSetWindowAttribute(
                hwnd.0 as _,
                attribute as u32,
                std::ptr::from_ref(&value).cast(),
                std::mem::size_of::<u32>() as u32,
            ) == 0
        }
    };
    set(DWMWA_CAPTION_COLOR, background)
        & set(DWMWA_TEXT_COLOR, text_for(background))
        & set(DWMWA_BORDER_COLOR, background)
}

#[cfg(not(windows))]
fn paint(_window: &WebviewWindow, _background: Rgb) -> bool {
    false
}

/// Paint `window`'s caption with a fixed colour; `painted` is whether the system accepted it
/// (Windows 11 does, earlier versions and other systems do not).
pub fn probe(window: &WebviewWindow) -> Value {
    serde_json::json!({ "painted": paint(window, [0x12, 0x34, 0x56]) })
}

/// Colour the caption and border of the calling window to match the page; `false` where the
/// system cannot (before Windows 11, or off Windows).
#[tauri::command]
pub fn set_window_chrome(window: WebviewWindow, args: Args) -> bool {
    args.as_ref()
        .and_then(|values| values.first())
        .and_then(background_of)
        .is_some_and(|background| paint(&window, background))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_background_is_three_numbers_in_range() {
        assert_eq!(
            background_of(&json!({ "background": [227, 221.4, 213] })),
            Some([227, 221, 213])
        );
        assert_eq!(
            background_of(&json!({ "background": [0, 0, 0] })),
            Some([0, 0, 0])
        );
        for bad in [
            json!({}),
            json!({ "background": [1, 2] }),
            json!({ "background": [1, 2, 3, 4] }),
            json!({ "background": [256, 0, 0] }),
            json!({ "background": [-1, 0, 0] }),
            json!({ "background": ["1", 2, 3] }),
            json!({ "background": "red" }),
            json!("red"),
        ] {
            assert_eq!(background_of(&bad), None, "{bad}");
        }
    }

    #[test]
    fn light_backgrounds_get_dark_text_and_dark_ones_light_text() {
        assert_eq!(text_for([227, 221, 213]), [0, 0, 0]);
        assert_eq!(text_for([255, 255, 255]), [0, 0, 0]);
        assert_eq!(text_for([42, 39, 36]), [255, 255, 255]);
        assert_eq!(text_for([0, 0, 0]), [255, 255, 255]);
        assert_eq!(text_for([98, 80, 213]), [255, 255, 255]);
    }

    #[test]
    fn a_colorref_puts_blue_in_the_high_byte() {
        assert_eq!(colorref([0x11, 0x22, 0x33]), 0x0033_2211);
        assert_eq!(colorref([255, 0, 0]), 0xff);
        assert_eq!(colorref([0, 0, 0]), 0);
    }
}
