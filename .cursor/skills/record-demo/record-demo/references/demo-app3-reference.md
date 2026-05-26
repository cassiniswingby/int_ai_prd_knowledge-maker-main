# demo-app3 リファレンス

シナリオ YAML を作成する際に参照するアプリ固有の情報。

## Demo A: 引き合い対応

- Route: `/demo-a`
- Patterns:
  - `pattern1`: 大型テンダー (Nordic Sea) — 33明細, CRA+13Cr混在
  - `pattern2`: 中規模テンダー (Middle East) — 8明細, 13Cr中心
- HITL flow (2 steps):
  1. `analyze` — 書類分析
  2. `inquiry_draft` — 引合連絡 ※ストリーミング表示あり
- 推奨 timeout: 60s

## Demo B: オダコン・入票

- Route: `/demo-b`
- Patterns:
  - `pattern1`: H-1分割あり (Falcon Oil) — 4品目/750MT, 分割発生
  - `pattern2`: 分割なし (Pacific Energy) — 3品目/300MT, 全品目150MT以下
  - `pattern3`: フォールバック確認 — 2品目/160MT, 最小構成
- HITL flow (3 steps):
  1. `po_analysis` — PO読み込み
  2. `rule_matching` — ルール照合
  3. `odakon` — オダコン生成
- 推奨 timeout: 90s

## 共通 UI セレクタ

### ナビゲーション
- トップページ: `/`
- Demo A タブ: `a[href="/demo-a"]`
- Demo B タブ: `a[href="/demo-b"]`

### アップロード画面
- サンプル選択ボタン: `button[data-scenario-id="{pattern_id}"]`
- ファイル入力: `#file-input`
- アップロードステータス: `#upload-status:not(.hidden)`

### ワークスペース画面
- 実行ボタン: `#btn-start`
- ワークスペース: `#phase-workspace`
- チャットログ: `#chat-log`

### HITL (Human-in-the-loop)
- HITL フッター: `#hitl-footer:not(.hidden)`
- 承認ボタン: `#hitl-footer button` (テキスト: "承認して次へ")
- 現在の step_id: JS グローバル変数 `currentStepId`

### 検知テキスト
- HITL レビュー: `{label}: 担当者の確認を待っています`
- ステップ完了: `{label}: 承認されました`
- ワークフロー完了: `全処理完了`

## HITL 検証メカニズム

HITL フッター表示時、JS グローバル変数 `currentStepId` に現在のステップ ID が格納される。
`page.evaluate("() => currentStepId")` で取得可能。

これにより、チャットログの文言に依存せず、構造的にステップ ID を検証できる。
