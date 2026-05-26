---
name: powerautomate-teams-csv
description: Power Automate で Teams チャンネルのログを CSV として OneDrive に出力するフローの構築・修正・pac CLI でのインポートを行うスキル。Teams データ収集フロー、Power Automate ソリューション管理、pac solution pack/import、CSVエラー修正、チャンネルID変更、null対策の実装に使用する。
---

# Power Automate Teams データ収集フロー

このスキルは `EC_Chatbot_teams-data-collection` ソリューション内の3フローを管理する。

## 構成概要

| フロー名 | チャンネル | 出力 CSV |
|---|---|---|
| Teams_契約料金エスカレーション | groupId: `118d13ca-...` / channelId: `19:5645b91c...` | `YYYYMMDD-YYYYMMDD_Teams_契約料金エスカレーション_ログ.csv` |
| Teams_updateチャネル | groupId: `118d13ca-...` / channelId: `19:9cc32da4...` | `YYYYMMDD-YYYYMMDD_Teams_updateチャネル_ログ.csv` |
| Teams_TGCSコンシェルジュ | groupId: `802bbbef-...` / channelId: `19:s7x-X2dm...` | `YYYYMMDD-YYYYMMDD_Teams_TGCSコンシェルジュ_ログ.csv` |

出力先 OneDrive: `/Teamsログ/`

## フロー構造

```
手動トリガー（開始日 / 終了日を入力: YYYY-MM-DD）
  ↓
変数初期化: replyText / start_date / end_date / csvContent（ヘッダー行含む）
  ↓
Get messages（最新1000件）
  ↓
Apply to each → Check date range（日付フィルタ）
  YES → List replies（$top:20）→ Apply to each 1（返信をreplyTextに追記）
       → Append csv row（csvContentに1行追記）
       → Set variable（replyTextリセット）
  NO  → スキップ
  ↓
Create CSV file（OneDrive /Teamsログ/ に新規作成）
```

## ファイル構成（pac CLI管理）

```
data/EC_Chatbot_current_unpacked/
├── Other/
│   ├── Solution.xml
│   └── Customizations.xml
└── Workflows/
    ├── Teams_-022CE842-...json        # TGCSコンシェルジュ
    ├── Teams_-9B844C9B-...json        # updateチャネル
    ├── Teams_-934F1EA6-...json        # 契約料金エスカレーション
    ├── Teams_-022CE842-...json.data.xml
    ├── Teams_-9B844C9B-...json.data.xml
    └── Teams_-934F1EA6-...json.data.xml
```

## pac CLI 操作

```powershell
# pack
pac solution pack --folder data/EC_Chatbot_current_unpacked --zipfile data/EC_Chatbot_vXX.zip --packagetype Unmanaged

# import
pac solution import --path data/EC_Chatbot_vXX.zip
```

- `Add-Content` エラーはターミナルのログ書き込み問題で無視してよい
- インポート後フローは **Off 状態**で作成される → Power Automate で `Turn on` が必要

## 接続設定（インポート後に必要）

フローを開いて以下の接続を設定：
- **Microsoft Teams**（Get messages / List replies）
- **OneDrive for Business**（Create CSV file）

ソリューション画面 → `接続参照` から設定するとフローエディタを開かずに済む。

## よくあるエラーと対処

### `replace` の第1引数が null（`InvalidTemplate`）

`subject` / `body/content` / `webUrl` / `displayName` は null になりえる。

**修正パターン:**
```json
// NG（nullでエラー）
"replace(items('Apply_to_each')?['subject'], '\"', '\"\"')"

// OK（coalesce でnullを空文字に）
"replace(replace(coalesce(items('Apply_to_each')?['subject'], ''), '\"', '\"\"'), decodeUriComponent('%0A'), ' ')"
```

### CSV が改行で崩れる

本文（`body/content`）に改行が含まれると CSV の行が分割される。

**修正:** `replace(..., decodeUriComponent('%0A'), ' ')` を追加。

### ファイル名 null

トリガー入力が空の場合 `replace(triggerBody()?['text'], '-', '')` がエラー。

**修正:** `replace(coalesce(triggerBody()?['text'], '00000000'), '-', '')`

### `MalformedFlowAssetFlowDefinition`

JSON 構文エラー。`expression` 内の条件式の書き方に問題がある場合が多い。

### ソリューションに新規フローが追加されない

Dataverse for Teams 環境では pac CLI でのフロー新規追加がブロックされることがある。

**対処:** My flows に pac import → `既存を追加` でソリューションに追加。

### 接続参照（つながり参照）が大量に蓄積

インポートのたびに `Customizations.xml` に接続参照が追加される。

**対処:** `Customizations.xml` の `<connectionreferences>` ブロックを空にしてからインポート:
```xml
<connectionreferences></connectionreferences>
```

## チャンネルID の変更方法

新しいチャンネルを追加する場合は JSON の以下の2箇所を変更：

```json
// Get messages
"parameters": {
  "groupId": "新しいgroupId",
  "channelId": "19:新しいchannelId@thread.tacv2"
}

// List replies of a channel message（同じ値を設定）
"parameters": {
  "groupId": "新しいgroupId",
  "channelId": "19:新しいchannelId@thread.tacv2",
  "messageId": "@items('Apply_to_each')?['id']"
}
```

チャンネルID の確認: Teams でチャンネルを右クリック → `チャンネルへのリンクを取得` → URL から抽出。

## 運用手順（定期データ取得）

1. Power Automate → `EC_Chatbot_teams-data-collection` ソリューションを開く
2. 対象フローを `Turn on`
3. `Run` → 開始日・終了日を入力（`YYYY-MM-DD` 形式）
4. フロー完了後、OneDrive `/Teamsログ/` フォルダに CSV が生成される
5. CSV をダウンロード → `data/teams_csv/` に配置
6. `build_chunks_from_teams_csv.py` でチャンク化
7. `build_index_and_upload.py` でインデックス更新

## CSV → チャンク変換

詳細は `scripts/build_chunks_from_teams_csv.py` を参照。

```bash
.venv/Scripts/python scripts/build_chunks_from_teams_csv.py \
  --csv data/teams_csv/<ファイル名>.csv \
  --channel "チャンネル名" \
  --team "共通"
```
