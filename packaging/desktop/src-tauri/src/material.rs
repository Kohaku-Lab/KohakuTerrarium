//! The Mica or Acrylic backdrop behind the page's translucent chrome (Windows 11 22H2 and later).

/// First Windows build whose window manager accepts a system backdrop type.
pub const MIN_BUILD: u32 = 22621;

/// Page attribute (`<html data-kt-material>`) that switches the chrome to translucent colours.
pub const PAGE_ATTRIBUTE: &str = "ktMaterial";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Material {
    None,
    Mica,
    Acrylic,
}

impl Material {
    pub fn name(self) -> &'static str {
        match self {
            Material::None => "none",
            Material::Mica => "mica",
            Material::Acrylic => "acrylic",
        }
    }
}

/// The material a `windowMaterial` setting gives on a system of Windows build `build`
/// (`None` off Windows): `auto` follows the system and uses Mica where it is supported.
pub fn resolve(setting: &str, build: Option<u32>) -> Material {
    if !build.is_some_and(|number| number >= MIN_BUILD) {
        return Material::None;
    }
    match setting {
        "none" => Material::None,
        "acrylic" => Material::Acrylic,
        _ => Material::Mica,
    }
}

/// Script that marks the page so its chrome draws translucent over the backdrop.
pub fn page_script(material: Material) -> Option<String> {
    (material != Material::None).then(|| {
        format!(
            "document.documentElement.dataset.{PAGE_ATTRIBUTE} = '{}';",
            material.name()
        )
    })
}

/// The Windows build number, read from the registry.
#[cfg(windows)]
pub fn windows_build() -> Option<u32> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;

    RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        .and_then(|key| key.get_value::<String, _>("CurrentBuildNumber"))
        .ok()
        .and_then(|text| text.trim().parse().ok())
}

#[cfg(not(windows))]
pub fn windows_build() -> Option<u32> {
    None
}

/// Put `material` behind `window`; `false` where the system refuses or there is nothing to apply.
#[cfg(windows)]
pub fn apply(window: &tauri::WebviewWindow, material: Material) -> bool {
    use windows_sys::Win32::Graphics::Dwm::{
        DwmExtendFrameIntoClientArea, DwmSetWindowAttribute, DWMSBT_MAINWINDOW,
        DWMSBT_TRANSIENTWINDOW, DWMWA_SYSTEMBACKDROP_TYPE,
    };
    use windows_sys::Win32::UI::Controls::MARGINS;

    let backdrop = match material {
        Material::None => return false,
        Material::Mica => DWMSBT_MAINWINDOW,
        Material::Acrylic => DWMSBT_TRANSIENTWINDOW,
    };
    let Ok(hwnd) = window.hwnd() else {
        return false;
    };
    let margins = MARGINS {
        cxLeftWidth: -1,
        cxRightWidth: -1,
        cyTopHeight: -1,
        cyBottomHeight: -1,
    };
    // SAFETY: `hwnd` is this window's handle; `margins` and `backdrop` outlive the calls and
    // DWM copies them.
    unsafe {
        DwmExtendFrameIntoClientArea(hwnd.0 as _, &margins) == 0
            && DwmSetWindowAttribute(
                hwnd.0 as _,
                DWMWA_SYSTEMBACKDROP_TYPE as u32,
                std::ptr::from_ref(&backdrop).cast(),
                std::mem::size_of_val(&backdrop) as u32,
            ) == 0
    }
}

#[cfg(not(windows))]
pub fn apply(_window: &tauri::WebviewWindow, _material: Material) -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_system_without_backdrop_support_never_gets_a_material() {
        for setting in ["auto", "mica", "acrylic", "none"] {
            assert_eq!(resolve(setting, None), Material::None, "{setting}");
            assert_eq!(resolve(setting, Some(MIN_BUILD - 1)), Material::None);
        }
    }

    #[test]
    fn auto_follows_the_system_and_the_others_are_taken_as_asked() {
        assert_eq!(resolve("auto", Some(MIN_BUILD)), Material::Mica);
        assert_eq!(resolve("mica", Some(26100)), Material::Mica);
        assert_eq!(resolve("acrylic", Some(26100)), Material::Acrylic);
        assert_eq!(resolve("none", Some(26100)), Material::None);
    }

    #[test]
    fn only_a_real_material_marks_the_page() {
        assert_eq!(page_script(Material::None), None);
        let script = page_script(Material::Acrylic).unwrap();
        assert!(script.contains("ktMaterial"));
        assert!(script.contains("'acrylic'"));
    }
}
