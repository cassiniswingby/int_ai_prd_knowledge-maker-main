---
name: record-demo
description: >-
  デモアプリの操作を Playwright で自動操作しながら動画録画する。
  シナリオ YAML で操作手順を定義し、WebM 動画を出力する。
  重要場面は自動キャプチャし、台本（caption）付きシナリオで分かりやすいデモを実現。
  Use when user says "録画", "record demo", "デモ動画", "シナリオを録画",
  "動画を撮って", "recording"。
metadata:
  author: pragmateches
  version: 4.0.0
  category: workflow-automation
  tags: [recording, demo, playwright, video, capture]
---

# record-demo

## Purpose

デモアプリの操作を Playwright で自動実行しながら動画録画するスキル。
ユーザーの指示（デモ名・パターン指定、または既存 YAML 指定）から
シナリオ YAML を特定・生成し、dry-run で検証してから録画を実行する。
**台本（caption）付きの分かりやすいシナリオ**を生成し、**重要場面を自動キャプチャ**する。

対応アプリ:
- **demo-app3**: 住友商事 AIエージェントデモ → `references/demo-app3-reference.md`
- **キョウデン demo-app**: 生産スケジュール承認デモ → `references/kyouden-reference.md`
- **その他**: ユーザーが URL・セレクタを指定すれば任意のWebアプリに対応可能

## Important

- シナリオ YAML をユーザーに提示してレビューしてもらってから録画すること
- セレクタは対応するアプリの reference ファイルの情報に基づくこと。推測しない
- YAML 生成時は `assets/scenario-template.yaml` をベースに使う
- シナリオ保存先・動画出力先はプロジェクトに合わせて決める（ユーザーに確認）
- キャプチャ出力先: `{output_dir}/{output_name}_captures/`

## Instructions

### Step 0: アプリ特定

ユーザーの指示から、どのアプリのデモを録画するか決める。

**決定木:**
1. アプリが明示されている → 対応する reference ファイルを参照
2. URL が指定されている → その URL を base_url として使用
3. 曖昧 → 確認質問する:
   - 「どのアプリのデモですか？」
   - 利用可能な reference ファイル一覧を提示

### Step 1: シナリオ特定

**決定木:**
1. 既存 YAML を指定された → そのまま使用（Step 3 へ）
2. デモ名・パターン名が指定された → Step 2 で YAML を生成
3. 曖昧な指示 → 確認質問する（reference ファイルのパターン一覧を参照）

### Step 2: シナリオ YAML 生成（既存 YAML がない場合）

1. 対応する reference ファイルでデモの route, セレクタ, フロー情報を確認
2. `assets/scenario-template.yaml` をベースに YAML を生成
3. **台本（caption）を書く** — 以下のガイドに従う
4. **キャプチャポイント（capture: true）を設定** — 以下のガイドに従う
5. 生成した YAML をユーザーに提示してレビュー
6. 承認されたらシナリオ保存先ディレクトリに保存

YAML フォーマットの詳細は `references/scenario-format.md` を参照。

#### 台本（caption）の書き方ガイド

`caption` はデモ動画に表示される字幕テキスト。**コアとなる場面だけに付ける。**
多すぎると視聴者が文字を追ってしまい、肝心のデモ内容が入ってこない。

**数の目安:** 30秒の動画で 4-5 個が上限。操作の都度ではなく、価値が伝わる場面に絞る。

**書き方のルール:**
- **業務/視聴者目線**で書く: 「何のボタンを押す」ではなく「業務として何が起きているか」
- **価値を伝える**: 自動化・効率化・透明性の見どころにフォーカス
- **短く簡潔に**: 1行20-30文字程度
- cp932 で表せない文字（em dash `---` 等）は使わない（Windows 環境制約）

**良い例:**
```yaml
caption: "引き合い対応業務のAI自動化デモ"
caption: "前回との差分がハイライトで表示されます"
caption: "4,158行から14件に自動絞り込み"
caption: "全処理完了 - 従来数時間の作業が数分に"
```

**悪い例:**
```yaml
caption: "ボタンをクリック"           # 何のボタンか不明
caption: "scroll_to を実行"           # 技術用語
caption: "承認して次へ進む"          # UIの操作をそのまま書いている
```

**caption を付けるべき場面:**

| 場面 | caption | capture |
|---|---|---|
| 導入（トップページ） | o（何のデモか） | o |
| データ投入・処理開始 | o（処理の意味） | o（投入後） |
| 結果確認（差分・ログ等） | o（価値を伝える） | o |
| 承認操作 | 不要 | |
| 完了 | o（インパクト） | o |

#### キャプチャポイントの設定ガイド

`capture: true` を付けたステップで、ステップ実行後にスクリーンショットが自動保存される。

**必ずキャプチャすべき場面:**
- トップページ（デモアプリの全体像）
- データ投入後（アップロード完了状態）
- 重要な結果表示画面
- 全処理完了画面

**キャプチャの出力:**
- 保存先: `output/{output_name}_captures/`
- ファイル名: `{step番号}_{caption}.png`

#### 字幕のスタイル仕様

**デフォルト: インライン JS キャプション（`caption_mode: js`）**

caption 付きステップの字幕は、Playwright 録画中にページ DOM へ直接注入される。
動画にそのまま映り込むため、ffmpeg 不要で WYSIWYG。

- **フォント**: BIZ UDPGothic > Noto Sans JP > sans-serif, 24px
- **色**: 白文字 (#fff)、黒背景 (rgba(0,0,0,0.82))
- **形状**: 角丸 8px、パディング 14px 36px
- **位置**: 画面下部中央 (fixed, bottom: 28px)
- **アニメーション**: 300ms フェードイン（上方向に10pxスライド）、500ms フェードアウト
- **最小表示時間**: 3.0秒（`caption_min_duration` で上書き可）

このスタイルは 2026-02-25 に承認済み。変更時はユーザー確認を取ること。

**オプション: ASS 字幕（`caption_mode: ass`）**

ffmpeg で ASS 字幕を動画に後処理で焼き込む方式。
録画後にタイミング調整が可能だが、ffmpeg が必要。

- BIZ UDPGothic, 32pt, Bold
- 白文字、黒背景（75%不透明）
- 300ms フェードイン/アウト
- 字幕間 0.2s ギャップ（自動）

### Step 3: 前提チェック

録画実行前に以下を確認する:

1. **アプリ起動確認**: `health_check: true`（デフォルト）なら `/health`、`false` なら base_url への HTTP 疎通
2. **Playwright**: `python -c "from playwright.async_api import async_playwright"`
3. **PyYAML**: `python -c "import yaml"`
4. **ffmpeg（ASS モード時のみ必須）**: `ffmpeg -version`

いずれかが失敗した場合、修正手順を提示:
- アプリ未起動 → シナリオ YAML の `base_url` で指定されたアプリを起動する
- Playwright → `pip install playwright && python -m playwright install chromium`
- PyYAML → `pip install pyyaml`
- ffmpeg → https://ffmpeg.org/download.html

### Step 4: dry-run 検証

```bash
python {skill_dir}/scripts/record.py --scenario {yaml_path} --dry-run
```

エラーが出た場合は YAML を修正して再検証する。

### Step 5: 録画実行

```bash
python {skill_dir}/scripts/record.py --scenario {yaml_path} --output {output_dir} --headed
```

- `{skill_dir}`: このスキルのディレクトリパス
- `{yaml_path}`: シナリオ YAML のパス
- `{output_dir}`: ユーザー指定の出力ディレクトリ
- `--headed`: ブラウザを表示して実行（デフォルト推奨）

### Step 6: 結果報告

- 成功時:
  - **動画**のパス（`{output_name}_{YYYYMMDD_HHMMSS}.webm`）
  - JS モード: キャプション込みの単一動画
  - ASS モード: Raw 動画 + 字幕付き動画 + ASS ファイル
  - **キャプチャ一覧**を報告
- 失敗時: エラーステップ、スクリーンショット、再実行コマンドを報告

## Examples

### Example 1: キョウデン demo-app を録画

User says: "キョウデンのデモを録画して"
Actions:
1. `references/kyouden-reference.md` を参照
2. SCENARIO.md がある場合はそれを参考にシナリオ YAML を生成
3. YAML をユーザーに提示してレビュー
4. 前提チェック → dry-run → 録画実行
Result:
- `output/kyouden_demo_YYYYMMDD_HHMMSS.webm` が出力される

### Example 2: demo-app3 の既存シナリオを録画

User says: "demo_a_basic.yaml を録画して"
Actions:
1. `scenarios/demo_a_basic.yaml` を読み込み
2. 前提チェック → dry-run → 録画実行
Result:
- `output/demo_a_basic_YYYYMMDD_HHMMSS.webm` が出力される

### Example 3: demo-app3 のデモ名指定

User says: "Demo B の pattern2 を録画して"
Actions:
1. `references/demo-app3-reference.md` を参照し Demo B / pattern2 の情報を取得
2. YAML を生成 → ユーザーレビュー → 承認後、録画実行

## Troubleshooting

### Error: アプリが応答しない
**Cause**: 対象アプリが起動していない
**Solution**: シナリオ YAML の `base_url` で指定されたアプリを起動する。`health_check: false` の場合、base_url への疎通を確認。

### Error: タイムアウト
**Cause**: 待機条件が満たされない（セレクタ不一致 or 処理遅延）
**Solution**: エラースクリーンショットを確認し、セレクタが reference ファイルと一致しているか検証。`timeout` を増やす。

### Error: upload ファイルが見つからない
**Cause**: file パスが不正（相対パスの基準はシナリオ YAML のディレクトリ）
**Solution**: YAML と同じディレクトリからの相対パス、または絶対パスで指定。

### Error: YAML パースエラー
**Cause**: YAML の構文ミス
**Solution**: `--dry-run` で再検証し、エラー箇所を修正。
