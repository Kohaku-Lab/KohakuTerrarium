use super::*;
use serde_json::json;

#[test]
fn pet_settings_follow_registered_window_identity_without_a_generation() {
    let windows = OverlayWindows::default();
    for generation in [Some("ready-generation"), None] {
        let mut spec = parse_spec(&json!({
            "label": "overlay-pet", "key": "ext:virtual-pet/pet",
            "width": 224, "height": 280, "generation": generation
        }))
        .unwrap();
        assert!(!windows.settings_allowed(&spec.label, "ext:virtual-pet/pets"));
        windows.register(&spec);
        assert!(windows.settings_allowed(&spec.label, "ext:virtual-pet/pets"));
        assert!(windows.settings_allowed(&spec.label, "overlays"));
        assert_eq!(
            windows.matches_generation(&spec.label, "ready-generation"),
            generation.is_some()
        );
        assert!(!windows.matches_generation(&spec.label, ""));
        assert!(!windows.matches_generation(&spec.label, "stale"));
        assert!(!windows.matches_generation("overlay-unknown", "ready-generation"));
        for section in [
            "",
            "auth",
            "ext:other/settings",
            "ext:virtual-pet/pets/extra",
        ] {
            assert!(!windows.settings_allowed(&spec.label, section));
        }
        assert!(!windows.settings_allowed("main", "ext:virtual-pet/pets"));
        assert!(!windows.settings_allowed("overlay-unknown", "ext:virtual-pet/pets"));
        windows.0.lock().unwrap().remove(&spec.label);
        assert!(!windows.settings_allowed(&spec.label, "ext:virtual-pet/pets"));
        assert!(!windows.matches_generation(&spec.label, "ready-generation"));
        spec.key = "ext:other/pet".into();
        windows.register(&spec);
        assert!(!windows.settings_allowed(&spec.label, "ext:virtual-pet/pets"));
        windows.0.lock().unwrap().remove(&spec.label);
    }
}

#[test]
fn capture_releases_and_represses_do_not_share_an_origin() {
    let press = |id: &str, x| Capture {
        id: id.into(),
        cursor: (x, 0.0),
        origin: (0.0, 0.0),
        scale: (1.0, 1.0),
    };
    assert_eq!(
        continue_capture(Some(press("one", 30.0)), Some(press("one", 10.0)))
            .unwrap()
            .cursor
            .0,
        10.0
    );
    assert_eq!(
        continue_capture(Some(press("two", 30.0)), Some(press("one", 10.0)))
            .unwrap()
            .cursor
            .0,
        30.0
    );
    assert!(continue_capture(None, Some(press("one", 10.0))).is_none());
}

#[test]
fn pointer_coordinates_include_negative_monitors_zoom_and_stationary_moves() {
    let press = json!({"x": 80.0, "y": 120.0});
    assert_eq!(
        press_point(Some(&press), (-1600.0, 100.0), (1.25, 1.25)),
        Some((-1500.0, 250.0))
    );
    assert_eq!(press_point(None, (0.0, 0.0), (1.0, 1.0)), None);
    let cursor = (-1500.0, 250.0);
    assert_eq!(
        local_point(cursor, (-1600.0, 100.0), (1.25, 1.25)),
        (80.0, 120.0)
    );
    assert_eq!(
        local_point(cursor, (-1600.0, 100.0), (2.5, 2.5)),
        (40.0, 60.0)
    );
    assert_eq!(
        local_point(cursor, (-1400.0, 100.0), (1.25, 1.25)),
        (-80.0, 120.0)
    );
    let press = (-1525.0, 225.0);
    assert_eq!(local_point(cursor, press, (1.25, 1.25)), (20.0, 20.0));
}

#[test]
fn monitor_reconciliation_preserves_reachable_positions_and_avoids_docks() {
    let area = Rect {
        x: -1920.0,
        y: -100.0,
        w: 1920.0,
        h: 1040.0,
    };
    assert_eq!(
        reachable_position((-1500.0, 100.0), (224.0, 280.0), area),
        (-1500.0, 100.0)
    );
    assert_eq!(
        reachable_position((-100.0, 900.0), (224.0, 280.0), area),
        (-224.0, 660.0)
    );
    assert_eq!(
        reachable_position((-3000.0, -400.0), (224.0, 280.0), area),
        (-1920.0, -100.0)
    );
}

#[test]
fn only_overlay_labels_are_accepted() {
    assert!(is_overlay_label("overlay-pets-cat"));
    assert!(!is_overlay_label("main"));
    assert!(!is_overlay_label("overlay-"));
    assert!(!is_overlay_label("overlay-Upper"));
    assert!(!is_overlay_label("overlay-a/b"));
    assert!(!is_overlay_label(&format!("overlay-{}", "a".repeat(49))));
}

#[test]
fn anchors_stay_inside_the_screen_with_a_margin() {
    let screen = (1000.0, 600.0);
    let size = (200.0, 100.0);
    assert_eq!(anchored_position("top-left", screen, size), (16.0, 16.0));
    assert_eq!(
        anchored_position("bottom-right", screen, size),
        (784.0, 484.0)
    );
    assert_eq!(anchored_position("top-right", screen, size), (784.0, 16.0));
    assert_eq!(
        anchored_position("bottom-left", screen, size),
        (16.0, 484.0)
    );
    assert_eq!(anchored_position("center", screen, size), (400.0, 250.0));
    assert_eq!(anchored_position("nonsense", screen, size), (784.0, 484.0));
}

#[test]
fn hit_regions_are_half_open_rectangles() {
    let regions = [Rect {
        x: 10.0,
        y: 10.0,
        w: 20.0,
        h: 20.0,
    }];
    assert!(hits(&regions, 10.0, 10.0));
    assert!(hits(&regions, 29.9, 29.9));
    assert!(!hits(&regions, 30.0, 20.0));
    assert!(!hits(&regions, 9.9, 20.0));
    assert!(!hits(&[], 0.0, 0.0));
}

#[test]
fn a_spec_needs_a_valid_label_key_and_size() {
    let good = json!({ "label": "overlay-a", "key": "ext:p/o", "width": 10, "height": 9999 });
    let spec = parse_spec(&good).unwrap();
    assert_eq!(spec.size, (MIN_SIZE, MAX_SIZE));
    assert!(spec.on_top && spec.position.is_none());
    assert_eq!(spec.anchor, "bottom-right");
    for bad in [
        json!({ "label": "main", "key": "k", "width": 100, "height": 100 }),
        json!({ "label": "overlay-a", "width": 100, "height": 100 }),
        json!({ "label": "overlay-a", "key": "k", "height": 100 }),
    ] {
        assert!(parse_spec(&bad).is_none());
    }
    let placed = json!({
        "label": "overlay-a", "key": "k", "width": 100, "height": 100,
        "x": 5, "y": 6, "alwaysOnTop": false, "anchor": "center"
    });
    let spec = parse_spec(&placed).unwrap();
    assert_eq!(spec.position, Some((5.0, 6.0)));
    assert!(!spec.on_top);
    assert_eq!(spec.anchor, "center");
}

#[test]
fn regions_are_read_from_a_list_of_quads() {
    let args: Args = Some(vec![json!([[1, 2, 3, 4], [5, 6], "x", [7, 8, 9, 10]])]);
    assert_eq!(
        parse_regions(&args),
        vec![
            Rect {
                x: 1.0,
                y: 2.0,
                w: 3.0,
                h: 4.0
            },
            Rect {
                x: 7.0,
                y: 8.0,
                w: 9.0,
                h: 10.0
            },
        ]
    );
    assert!(parse_regions(&None).is_empty());
}

#[test]
fn the_overlay_url_carries_the_key_as_one_encoded_parameter() {
    let url = overlay_url(8001, "ext:pets/cat&x=1").unwrap();
    assert_eq!(url.host_str(), Some("127.0.0.1"));
    assert_eq!(url.port(), Some(8001));
    let pairs: Vec<_> = url.query_pairs().collect();
    assert_eq!(pairs.len(), 1);
    assert_eq!(pairs[0].0, OVERLAY_PARAM);
    assert_eq!(pairs[0].1, "ext:pets/cat&x=1");
}
