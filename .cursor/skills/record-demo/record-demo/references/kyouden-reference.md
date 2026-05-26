# キョウデン demo-app リファレンス

生産スケジュール承認デモ（FastAPI + HTMX）。
シナリオ YAML を作成する際に参照するアプリ固有の情報。

## アプリ概要

- **名称**: 生産スケジュール半自動承認デモ
- **技術**: FastAPI + Jinja2 + HTMX
- **base_url**: `http://localhost:8000`
- **起動**: `cd ws/demo-app/app && python main.py`
- **health_check**: `false`（/health エンドポイントなし）
- **推奨 timeout**: 30s
- **推奨 init_bg**: `#f8f9fa`（明るい背景のアプリ）

## デモフロー

SCENARIO.md（`ws/demo-app/docs/SCENARIO.md`）に基づく 7 ステップ:

1. ダッシュボード表示（30秒）
2. アップロード → 自動処理アニメーション（30秒）
3. 変更の詳細（差分テーブル）★最重要（1.5分）
4. 自動処理ログ（フローツリー）（1分）
5. 承認 → ダウンロードリンク（30秒）
6. 差戻し（コメント入力）（30秒）
7. まとめ（30秒）

## ルート

| パス | 画面 |
|------|------|
| `/` | ダッシュボード（本日の処理状況） |
| `/upload` | データアップロード |
| `/approve` | 承認画面（差分テーブル・ログ・承認/差戻し） |
| `/reset` | デモ状態リセット |
| `/approve/download/symbol` | シンボルExcelダウンロード |
| `/approve/download/press` | プレスExcelダウンロード |

## UI セレクタ

### ダッシュボード（`/`）
- 1回目カード（アップロード待ち）: `.run-card-pending`
- 1回目カード（承認済み）: `.run-card-done`
- 2/3回目カード: `.run-card-waiting`
- 処理履歴トグル: `#toggle-today-log`
- リセットリンク: `a[href="/reset"]`

### アップロード画面（`/upload`）
- ファイル入力: `input[type="file"]`
- アップロードエリア: `#upload-area`
- 処理中表示: `#processing`
- 処理ログ: `#processing-log`

### 承認画面（`/approve`）
- ヘッダーバッジ: `#header-badge`
- **変更の詳細**:
  - カードトグル: `#toggle-diff-detail`
  - カードボディ: `#diff-detail`
  - 差分テーブル: `.diff-table`
  - 追加行: `.row-added`
  - 変更行: `.row-modified`
  - 削除行: `.row-deleted`
  - 要注意行: `.row-warn`
  - 判定ロジックセル: `.logic-cell`
- **アクションバー**:
  - 承認ボタン: `.btn-approve`
  - 差戻しボタン: `.btn-reject`
  - ダウンロードリスト: `.dl-list`
- **スケジュールテーブル**:
  - シンボルテーブルトグル: `#toggle-table-symbol`
  - プレステーブルトグル: `#toggle-table-press`
- **処理ログ**:
  - ログトグル: `#toggle-log-detail`
  - ログボディ: `#log-detail`
  - フローツリー: `.flow-tree`
  - ルートノード: `.flow-root`
  - 除外ノード: `.flow-exclude`
  - 残件ノード: `.flow-remain`
  - 結果ノード: `.flow-result`
  - 警告ノード: `.flow-warn`
- **差戻しモーダル**:
  - モーダル: `#reject-modal`
  - コメント入力: `#reject-comment`
  - 差戻し確定ボタン: `button` (テキスト: "差戻し確定")
  - キャンセル: `.btn-cancel`

## サンプルデータ

| ファイル | 用途 |
|---------|------|
| `app/data/sample_symbol.xlsx` | 当日シンボルスケジュール（8行） |
| `app/data/sample_press.xlsx` | 当日プレススケジュール（6行） |
| `app/data/prev_symbol.xlsx` | 前日シンボル（差分計算用） |
| `app/data/prev_press.xlsx` | 前日プレス（差分計算用） |
| `app/data/納期・生産進捗情報_20260225.xlsx` | アップロード用元データ |

## 注意事項

- アプリの状態はインメモリ（サーバー再起動でリセット）
- `/reset` で手動リセット可能
- アップロード後の自動処理アニメーションは約 4.5 秒（JS setTimeout）
- アニメーション完了後、自動で `/approve` に遷移する
- 承認済み状態で `/upload` に再アクセスすると自動リセットされる
