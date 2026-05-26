# シナリオ YAML フォーマット仕様 (v4.0)

## 構造

```yaml
config:
  base_url: "http://localhost:8000"   # 必須: アプリの URL
  width: 1280                          # 任意: 動画幅 (default: 1280)
  height: 720                          # 任意: 動画高さ (default: 720)
  output_name: "demo_a_basic"          # 必須: 出力ファイル名（拡張子なし）
  timeout: 60                          # 任意: 各ステップの最大待機秒数 (default: 60)
  caption_min_duration: 3.0            # 任意: キャプション最小表示秒数 (default: 3.0)
  caption_mode: "js"                   # 任意: "js"(default) or "ass"
  caption_font_size: 24                # 任意: JS モード時のフォントサイズ px (default: 24)
  health_check: true                   # 任意: true=/health, false=base_url疎通 (default: true)
  init_bg: "#1a1a2e"                   # 任意: 録画開始時の背景色 (default: #1a1a2e)

steps:
  - action: <アクション名>
    <パラメータ>...
```

## v3 → v4 互換性

- `caption_mode` 未指定 → `"js"` がデフォルト（v3 では ASS が暗黙のデフォルト）
- 既存 YAML はそのまま動作する。ASS モードを使いたい場合は `caption_mode: "ass"` を明示
- 新アクション（fill, upload, scroll_to, highlight, wait_for_url）は v4 で追加。v3 の YAML には影響なし

## 共通フィールド（全アクション共通）

```yaml
- action: <アクション名>
  caption: "AIが書類を自動分析"   # 任意: 動画に表示する字幕テキスト
  capture: true                   # 任意: true でステップ実行後にスクリーンショットを保存
  pause_after: 3.0                # 任意: ステップ後の待機秒数 (default: 0)
```

## アクション一覧

### `goto` - ページ遷移

```yaml
- action: goto
  url: /demo-a              # 必須: 遷移先パス（base_url からの相対）
  wait_until: networkidle    # 任意: load | domcontentloaded | networkidle (default: networkidle)
```

### `click` - 要素クリック

```yaml
- action: click
  selector: "#btn-start"           # 必須: CSS セレクタ
  has_text: "実行"                  # 任意: テキストフィルタ（部分一致）
  wait_for: "#phase-workspace"     # 任意: クリック後にこのセレクタが visible になるまで待機
```

### `fill` - テキスト入力 (v4)

```yaml
- action: fill
  selector: "#reject-comment"                    # 必須: CSS セレクタ
  value: "K26-004 は顧客から保留連絡あり"         # 必須: 入力するテキスト
```

### `upload` - ファイルアップロード (v4)

```yaml
- action: upload
  selector: 'input[type="file"]'                 # 必須: file input の CSS セレクタ
  file: "data/納期・生産進捗情報_20260225.xlsx"    # 必須: ファイルパス（相対=YAML基準）
```

相対パスはシナリオ YAML ファイルのディレクトリからの相対パスとして解決される。
ファイルが存在しない場合はエラー（FileNotFoundError）。

### `scroll_to` - スムーズスクロール (v4)

```yaml
- action: scroll_to
  selector: ".diff-table"     # 必須: スクロール先の CSS セレクタ
```

要素が見つからない場合は WARNING を出力してスキップ（録画は継続）。

### `highlight` - 要素ハイライト (v4)

```yaml
- action: highlight
  selector: ".row-added"      # 必須: ハイライト対象の CSS セレクタ
  duration: 2.0               # 任意: ハイライト表示秒数 (default: 2.0)
```

赤枠（3px solid #e63946）で要素を一時的にハイライト。要素が見つからない場合は無視。

### `wait_for_url` - URL 遷移待ち (v4)

```yaml
- action: wait_for_url
  url_pattern: "/approve"     # 必須: 待機する URL パターン（部分一致）
```

`page.wait_for_url("**/approve**")` として動作し、遷移後に `networkidle` まで待つ。

### `wait_for_hitl` - HITL 承認バー待機

```yaml
- action: wait_for_hitl
  step_id: analyze           # 任意: JS の currentStepId と厳密一致で検証
  step_label: 書類分析        # 任意: チャットログのレビュー行にラベルが含まれるか検証
```

動作:
1. `#hitl-footer:not(.hidden)` が visible になるまで待機
2. チャットログに「確認を待っています」が出現するまで待機
3. `step_id` 指定時: JS グローバル変数 `currentStepId` と厳密一致で検証
4. `step_label` 指定時: チャットログの最新レビュー行にラベルが含まれるか検証

### `approve` - 承認ボタンクリック

```yaml
- action: approve
```

動作: `#hitl-footer` 内の「承認して次へ」ボタンをクリック

### `wait_for_complete` - ワークフロー完了待機

```yaml
- action: wait_for_complete
```

動作: チャットログに「全処理完了」が出現するまで待機

### `pause` - 固定待機

```yaml
- action: pause
  duration: 2.0              # 必須: 待機秒数
```

### `screenshot` - スクリーンショット撮影

```yaml
- action: screenshot
  caption: "最終結果のキャプチャ"  # 任意: ファイル名ラベル
```

## 字幕の動作仕様

### デフォルト: インライン JS キャプション（`caption_mode: js`）

caption 付きステップの字幕は、Playwright 録画中にページ DOM へ直接注入される。
動画にそのまま映り込むため、ffmpeg 不要で WYSIWYG。

**スタイル（固定 - 2026-02-25 承認）:**
- フォント: BIZ UDPGothic > Noto Sans JP > sans-serif, 24px
- 色: 白文字 (#fff)、黒背景 (rgba(0,0,0,0.82))
- 形状: 角丸 8px、パディング 14px 36px
- 位置: 画面下部中央 (fixed, bottom: 28px)
- アニメーション: 300ms フェードイン（上方向に10pxスライド）、500ms フェードアウト

**タイミング:**
- 表示開始: アクション実行直前
- 表示時間: `pause_after` と `caption_min_duration` のうち大きい方
- 前のキャプションは自動削除

### オプション: ASS 字幕（`caption_mode: ass`）

録画後に ffmpeg で ASS 字幕を焼き込む方式。タイミング調整が後処理で可能。

**スタイル:**
- フォント: BIZ UDPGothic, 32pt, Bold
- 色: 白文字、黒背景（75%不透明）
- フェード: 300ms フェードイン/アウト
- 字幕間ギャップ: 0.2秒（自動）
- 重なり解消: 最低 3 秒を維持しつつ自動調整

## バリデーションルール

- `config.base_url`: 必須。`http://` or `https://` で始まること
- `config.output_name`: 必須。ファイル名禁止文字 (`<>:"/\|?*`) を含まないこと
- `config.timeout`: 任意。正の数
- `config.caption_min_duration`: 任意。正の数 (default: 3.0)
- `config.caption_mode`: 任意。`"js"` or `"ass"` (default: `"js"`)
- `config.health_check`: 任意。true or false (default: true)
- `config.width`, `config.height`: 任意。正の整数
- `steps`: 1つ以上のステップが必要
- 各ステップの `action`: 12種のいずれか
- アクション固有の必須パラメータ（上記各アクションの説明を参照）

## 完全な例（キョウデン demo-app）

```yaml
config:
  base_url: "http://localhost:8000"
  width: 1280
  height: 800
  output_name: "kyouden_demo"
  timeout: 30
  caption_mode: "js"
  health_check: false
  init_bg: "#f8f9fa"

steps:
  # ── リセット ──
  - action: goto
    url: /reset
    pause_after: 1.0

  # ── STEP 1: ダッシュボード ──
  - action: goto
    url: /
    caption: "毎朝開く画面。本日の処理状況が一目で分かります"
    capture: true
    pause_after: 4.0

  - action: highlight
    selector: ".run-card-pending"
    duration: 3.0

  - action: scroll_to
    selector: ".run-card-waiting"
    caption: "本日は3回の抽出が予定されています"
    pause_after: 3.0

  # ── STEP 2: アップロード ──
  - action: click
    selector: ".run-card-pending"
    pause_after: 1.0

  - action: upload
    selector: 'input[type="file"]'
    file: "app/data/納期・生産進捗情報_20260225.xlsx"
    caption: "Excelをアップロードすると自動処理が始まります"
    pause_after: 1.0

  - action: wait_for_url
    url_pattern: "/approve"
    pause_after: 1.0

  # ── STEP 3: 差分テーブル（★最重要） ──
  - action: scroll_to
    selector: ".diff-table"
    caption: "変わったところだけ確認すればOK"
    capture: true
    pause_after: 4.0

  - action: highlight
    selector: ".row-added"
    duration: 2.0
    caption: "緑 = 追加された行"
    pause_after: 3.0

  - action: highlight
    selector: ".row-modified"
    duration: 2.0
    caption: "黄色 = 変更された行。判定ロジックも表示"
    pause_after: 4.0

  # ── STEP 4: 処理ログ ──
  - action: click
    selector: "#toggle-log-detail"
    pause_after: 1.0

  - action: scroll_to
    selector: ".flow-tree"
    caption: "4,158行から14件に絞り込む過程が全部見える"
    capture: true
    pause_after: 5.0

  # ── STEP 5: 承認 ──
  - action: scroll_to
    selector: ".action-bar"
    pause_after: 1.0

  - action: click
    selector: ".btn-approve"
    caption: "承認完了。今と同じ書式のExcelが出力されます"
    capture: true
    pause_after: 4.0

  # ── STEP 6: 差戻し（リセット→再実行） ──
  - action: goto
    url: /reset
    pause_after: 1.0

  - action: goto
    url: /upload
    pause_after: 1.0

  - action: upload
    selector: 'input[type="file"]'
    file: "app/data/納期・生産進捗情報_20260225.xlsx"
    pause_after: 1.0

  - action: wait_for_url
    url_pattern: "/approve"
    pause_after: 1.0

  - action: scroll_to
    selector: ".btn-reject"
    pause_after: 0.5

  - action: click
    selector: ".btn-reject"
    caption: "問題があれば差戻しもできます"
    pause_after: 1.0

  - action: fill
    selector: "#reject-comment"
    value: "K26-004 は顧客から保留連絡あり。スケジュールから除外してほしい。"
    pause_after: 2.0

  - action: click
    selector: "button"
    has_text: "差戻し確定"
    pause_after: 2.0

  # ── STEP 7: まとめ ──
  - action: goto
    url: /
    caption: "45分の作業が5分に。これが半自動承認です。"
    capture: true
    pause_after: 5.0
```
