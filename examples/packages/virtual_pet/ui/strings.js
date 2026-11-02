import { useLocale } from "@kohakuterrarium/ui-sdk";

export const messages = {
  en: {
    pets: "Virtual pets",
    companion: "Virtual pet",
    library: "Your pets",
    libraryHint:
      "Choose a companion. Selecting a pet updates the overlay on this backend.",
    import: "Import a pet",
    saving: "Saving…",
    files: "Supported pet files",
    filesHint:
      "Codex V1/V2 ZIP, up to 8 MiB, containing pet.json and a transparent PNG or WebP sprite sheet. A failed import keeps your current selection.",
    empty:
      "Bring your first companion home. Import a pet ZIP to get started; no running session is needed.",
    scope:
      "Saved for your account on {backend}. Your library and selection are shared across sessions on this backend, not across backends.",
    backend: "this backend",
    preview: "Current companion",
    previewHint: "Idle preview, independent of session activity.",
    selected: "Selected",
    select: "Choose {name}",
    remove: "Remove this pet",
    removeTitle: "Remove pet",
    removeConfirm:
      "Remove {name}? Another pet will be selected if available. Removing the last pet leaves no companion selected.",
    loading: "Opening your pets…",
    previewLoading: "Loading preview…",
    loadFailed: "Couldn't open your pets.",
    previewFailed:
      "Couldn't load this preview. Try again or choose another pet.",
    imageFailed: "Couldn't load your pet.",
    actionFailed:
      "Couldn't save this change. Your library has not been refreshed; try again.",
    retry: "Try again",
    settings: "Pet settings",
    hide: "Hide pet",
    choose: "Choose your companion",
    dismiss: "Dismiss message",
    running: "Processing",
    waiting: "Input required",
    ready: "Response ready",
    failed: "Task failed",
    limits: "Choose a pet ZIP no larger than 8 MiB.",
    overlayTitle: "Overlay visibility and position",
    overlayHint:
      "Choosing a pet does not unhide its overlay. Visibility and position are saved on this device by the host.",
    overlayControls: "Open overlay settings",
  },
  "zh-CN": {
    pets: "虚拟宠物",
    companion: "虚拟宠物",
    library: "你的宠物",
    libraryHint: "选择一位伙伴。选择会更新此后端的宠物浮层。",
    import: "导入宠物",
    saving: "正在保存…",
    files: "支持的宠物文件",
    filesHint:
      "Codex V1/V2 ZIP，最大 8 MiB，包含 pet.json 和透明 PNG 或 WebP 精灵图。导入失败不会改变当前选择。",
    empty: "迎接你的第一位伙伴。导入宠物 ZIP 即可开始，无需运行中的会话。",
    scope:
      "保存在你于{backend}的账户下。宠物库和选择在此后端的会话间共享，不跨后端共享。",
    backend: "此后端",
    preview: "当前伙伴",
    previewHint: "待机预览，与会话活动无关。",
    selected: "已选择",
    select: "选择{name}",
    remove: "移除此宠物",
    removeTitle: "移除宠物",
    removeConfirm:
      "移除{name}？如果还有其它宠物，将自动选择另一只；移除最后一只后将不再选择任何伙伴。",
    loading: "正在打开宠物库…",
    previewLoading: "正在加载预览…",
    loadFailed: "无法打开宠物库。",
    previewFailed: "无法加载预览，请重试或选择其它宠物。",
    imageFailed: "无法加载宠物。",
    actionFailed: "无法保存此更改，宠物库尚未刷新，请重试。",
    retry: "重试",
    settings: "宠物设置",
    hide: "隐藏宠物",
    choose: "选择你的伙伴",
    dismiss: "关闭提示",
    running: "处理中",
    waiting: "需要输入",
    ready: "回复就绪",
    failed: "任务失败",
    limits: "请选择不大于 8 MiB 的宠物 ZIP。",
    overlayTitle: "浮层显示与位置",
    overlayHint:
      "选择宠物不会自动显示已隐藏的浮层。显示与位置由宿主保存在当前设备。",
    overlayControls: "打开浮层设置",
  },
  "zh-TW": {
    pets: "虛擬寵物",
    companion: "虛擬寵物",
    library: "你的寵物",
    libraryHint: "選擇一位夥伴。選擇會更新此後端的寵物浮層。",
    import: "匯入寵物",
    saving: "正在儲存…",
    files: "支援的寵物檔案",
    filesHint:
      "Codex V1/V2 ZIP，最大 8 MiB，包含 pet.json 和透明 PNG 或 WebP 精靈圖。匯入失敗不會改變目前選擇。",
    empty:
      "迎接你的第一位夥伴。匯入寵物 ZIP 即可開始，不需要執行中的工作階段。",
    scope:
      "儲存在你於{backend}的帳戶下。寵物庫和選擇在此後端的工作階段間共用，不跨後端共用。",
    backend: "此後端",
    preview: "目前夥伴",
    previewHint: "待機預覽，與工作階段活動無關。",
    selected: "已選擇",
    select: "選擇{name}",
    remove: "移除此寵物",
    removeTitle: "移除寵物",
    removeConfirm:
      "移除{name}？如果還有其他寵物，將自動選擇另一隻；移除最後一隻後將不再選擇任何夥伴。",
    loading: "正在開啟寵物庫…",
    previewLoading: "正在載入預覽…",
    loadFailed: "無法開啟寵物庫。",
    previewFailed: "無法載入預覽，請重試或選擇其他寵物。",
    imageFailed: "無法載入寵物。",
    actionFailed: "無法儲存此變更，寵物庫尚未重新整理，請重試。",
    retry: "重試",
    settings: "寵物設定",
    hide: "隱藏寵物",
    choose: "選擇你的夥伴",
    dismiss: "關閉提示",
    running: "處理中",
    waiting: "需要輸入",
    ready: "回覆就緒",
    failed: "工作失敗",
    limits: "請選擇不大於 8 MiB 的寵物 ZIP。",
    overlayTitle: "浮層顯示與位置",
    overlayHint:
      "選擇寵物不會自動顯示已隱藏的浮層。顯示與位置由宿主儲存在目前裝置。",
    overlayControls: "開啟浮層設定",
  },
  ja: {
    pets: "バーチャルペット",
    companion: "バーチャルペット",
    library: "ペット",
    libraryHint:
      "仲間を選ぶと、このバックエンドのペットオーバーレイに反映されます。",
    import: "ペットをインポート",
    saving: "保存中…",
    files: "対応ファイル",
    filesHint:
      "pet.json と透過 PNG または WebP スプライトシートを含む Codex V1/V2 ZIP（最大 8 MiB）。失敗時は現在の選択を維持します。",
    empty:
      "最初の仲間を迎えましょう。ペット ZIP をインポートしてください。実行中のセッションは不要です。",
    scope:
      "{backend} のアカウントに保存されます。同じバックエンドのセッション間で共有されますが、他のバックエンドとは共有されません。",
    backend: "このバックエンド",
    preview: "現在の仲間",
    previewHint: "セッションとは独立した待機プレビューです。",
    selected: "選択中",
    select: "{name}を選択",
    remove: "このペットを削除",
    removeTitle: "ペットの削除",
    removeConfirm:
      "{name}を削除しますか？他のペットがあれば自動で選択します。最後のペットを削除すると選択は空になります。",
    loading: "ペットを読み込み中…",
    previewLoading: "プレビューを読み込み中…",
    loadFailed: "ペットを開けませんでした。",
    previewFailed:
      "プレビューを読み込めません。再試行するか他のペットを選んでください。",
    imageFailed: "ペットを読み込めませんでした。",
    actionFailed:
      "変更を保存できませんでした。ライブラリは未更新です。再試行してください。",
    retry: "再試行",
    settings: "ペット設定",
    hide: "ペットを非表示",
    choose: "仲間を選ぶ",
    dismiss: "メッセージを閉じる",
    running: "処理中",
    waiting: "入力が必要",
    ready: "応答の準備完了",
    failed: "タスク失敗",
    limits: "8 MiB 以下のペット ZIP を選択してください。",
    overlayTitle: "オーバーレイの表示と位置",
    overlayHint:
      "ペットを選んでも非表示のオーバーレイは再表示されません。表示と位置はこの端末に保存されます。",
    overlayControls: "オーバーレイ設定を開く",
  },
  de: {
    pets: "Virtuelle Haustiere",
    companion: "Virtuelles Haustier",
    library: "Deine Haustiere",
    libraryHint: "Wähle einen Begleiter für das Overlay dieses Backends.",
    import: "Haustier importieren",
    saving: "Wird gespeichert…",
    files: "Unterstützte Dateien",
    filesHint:
      "Codex-V1/V2-ZIP bis 8 MiB mit pet.json und transparentem PNG- oder WebP-Spritebogen. Bei einem Fehler bleibt die Auswahl erhalten.",
    empty:
      "Importiere eine Haustier-ZIP für deinen ersten Begleiter. Eine laufende Sitzung ist nicht nötig.",
    scope:
      "Für dein Konto auf {backend} gespeichert. Bibliothek und Auswahl gelten für dessen Sitzungen, nicht für andere Backends.",
    backend: "diesem Backend",
    preview: "Aktueller Begleiter",
    previewHint: "Ruhevorschau, unabhängig von Sitzungsaktivitäten.",
    selected: "Ausgewählt",
    select: "{name} auswählen",
    remove: "Dieses Haustier entfernen",
    removeTitle: "Haustier entfernen",
    removeConfirm:
      "{name} entfernen? Ein anderes verfügbares Haustier wird ausgewählt. Nach dem letzten bleibt die Auswahl leer.",
    loading: "Haustiere werden geöffnet…",
    previewLoading: "Vorschau wird geladen…",
    loadFailed: "Haustiere konnten nicht geöffnet werden.",
    previewFailed:
      "Vorschau nicht verfügbar. Erneut versuchen oder ein anderes Haustier wählen.",
    imageFailed: "Haustier konnte nicht geladen werden.",
    actionFailed:
      "Änderung konnte nicht gespeichert werden. Die Bibliothek wurde nicht aktualisiert. Bitte erneut versuchen.",
    retry: "Erneut versuchen",
    settings: "Haustiereinstellungen",
    hide: "Haustier ausblenden",
    choose: "Begleiter auswählen",
    dismiss: "Hinweis schließen",
    running: "Verarbeitung läuft",
    waiting: "Eingabe erforderlich",
    ready: "Antwort bereit",
    failed: "Aufgabe fehlgeschlagen",
    limits: "Wähle eine Haustier-ZIP bis 8 MiB.",
    overlayTitle: "Overlay-Sichtbarkeit und Position",
    overlayHint:
      "Eine Auswahl zeigt ein ausgeblendetes Overlay nicht erneut an. Sichtbarkeit und Position werden auf diesem Gerät gespeichert.",
    overlayControls: "Overlay-Einstellungen öffnen",
  },
  ko: {
    pets: "가상 반려동물",
    companion: "가상 반려동물",
    library: "내 반려동물",
    libraryHint: "동반자를 선택하면 이 백엔드의 오버레이에 반영됩니다.",
    import: "반려동물 가져오기",
    saving: "저장 중…",
    files: "지원 파일",
    filesHint:
      "pet.json과 투명 PNG 또는 WebP 스프라이트가 포함된 Codex V1/V2 ZIP, 최대 8 MiB. 실패하면 현재 선택을 유지합니다.",
    empty:
      "첫 동반자를 맞이하세요. 반려동물 ZIP을 가져오면 됩니다. 실행 중인 세션은 필요 없습니다.",
    scope:
      "{backend}의 계정에 저장됩니다. 이 백엔드의 세션 간에는 공유되지만 다른 백엔드와는 공유되지 않습니다.",
    backend: "이 백엔드",
    preview: "현재 동반자",
    previewHint: "세션 활동과 무관한 대기 미리보기입니다.",
    selected: "선택됨",
    select: "{name} 선택",
    remove: "이 반려동물 삭제",
    removeTitle: "반려동물 삭제",
    removeConfirm:
      "{name}을(를) 삭제할까요? 다른 반려동물이 있으면 자동으로 선택합니다. 마지막 반려동물을 삭제하면 선택이 비워집니다.",
    loading: "반려동물 여는 중…",
    previewLoading: "미리보기 로딩 중…",
    loadFailed: "반려동물을 열 수 없습니다.",
    previewFailed:
      "미리보기를 불러올 수 없습니다. 다시 시도하거나 다른 반려동물을 선택하세요.",
    imageFailed: "반려동물을 불러올 수 없습니다.",
    actionFailed:
      "변경을 저장하지 못했습니다. 라이브러리가 갱신되지 않았습니다. 다시 시도하세요.",
    retry: "다시 시도",
    settings: "반려동물 설정",
    hide: "반려동물 숨기기",
    choose: "동반자 선택",
    dismiss: "메시지 닫기",
    running: "처리 중",
    waiting: "입력 필요",
    ready: "응답 준비됨",
    failed: "작업 실패",
    limits: "8 MiB 이하의 반려동물 ZIP을 선택하세요.",
    overlayTitle: "오버레이 표시와 위치",
    overlayHint:
      "반려동물을 선택해도 숨겨진 오버레이는 표시되지 않습니다. 표시와 위치는 이 기기에 저장됩니다.",
    overlayControls: "오버레이 설정 열기",
  },
};

export function text(locale, key, params = {}) {
  const catalog =
    messages[locale] || messages[locale?.split("-")[0]] || messages.en;
  return (catalog[key] || messages.en[key] || key).replace(
    /\{(\w+)\}/g,
    (_, name) => String(params[name] ?? ""),
  );
}

export function usePetText() {
  const locale = useLocale();
  return (key, params) => text(locale.value, key, params);
}
