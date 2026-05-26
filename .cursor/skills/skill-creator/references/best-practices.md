---
type: inbox
added: 2026-01-06
themes: [skills]
source_type: other
url: ""
keywords: []
summary: "Agent Skills標準の概要と設計思想（Progressive Disclosure）、作成・運用のベストプラクティスを整理。"
tools: []
---

# Agent SkillsによるAIエージェント能力拡張のベストプラクティス

## 概要: Agent Skillsとは何か

Agent Skills（エージェントスキル）とは、AIエージェントに新たな専門知識や手順を教え込むためのモジュール化されたスキルパッケージです[1]。具体的には、各スキルはフォルダ単位で管理され、その中にエージェント向けの手順書（`SKILL.md`）や実行可能なスクリプト、リファレンス資料などを含みます[2]。Anthropic社がClaude向けに開発した仕組みですが、2025年12月にオープン標準として公開され、MicrosoftやOpenAI、Atlassian、Figma、Cursor、GitHubなど幅広いプラットフォームが採用しています[3]。これにより、一度作成したスキルを異なるAIエージェント間で再利用できる互換性が実現されています[4]。

Agent Skillsの狙いは、AIエージェントを汎用から専門特化へと変身させることです[5]。高度な汎用モデルであっても実務で安定した成果を出すには、手順的な知識や組織固有の文脈が不足しがちです[6]。Skillsはそうした追加知識をパッケージ化し、必要な時に読み込ませることで、エージェントがより正確かつ効率的にタスクを遂行できるようにします[7]。例えばLegalドメインの専門知識や社内のデータ分析手順、特定ツールの操作ノウハウなどをスキル化すれば、エージェントは該当タスク時にその知識を動的に取り込んで対応できます[8]。結果として、一度スキルを作れば複数のエージェント製品で再利用でき、企業は組織知をポータブルかつバージョン管理可能な形で蓄積できる利点があります[8]。

---

## 設計思想とスキルの構成要素

Agent Skillsの核心的な設計思想は「逐次的開示（Progressive Disclosure）」です[9]。これはエージェントへの指示や情報を一度に大量投入するのではなく、必要に応じて段階的に読み込むことでコンテキストウィンドウ（モデルが一度に保持できるテキスト量）の制約を克服しようというアプローチです[10]。具体的にはエージェント起動時点では各スキルのメタデータ（名前と説明）だけを読み込み、どんなスキルがあり何に使えそうかを把握します[11]。ユーザーからの指示内容を見て「必要だ」と判断したときに、該当スキルの`SKILL.md`本文（詳細な手順や知識）を読み込み[12]、さらに必要に応じて付随する参照ファイルやスクリプトを個別に開く、という段階を踏みます[13]。これにより関係ないスキル情報でコンテキストが埋まるのを防ぎつつ、必要な知識は逃さず提供できるのです[14]。

### スキルの基本構成

スキルの基本構成は以下の通りです[15]：

#### SKILL.md（必須）

スキルの中核となるMarkdown文書です。冒頭にYAMLフロントマターで`name`（スキルID）と`description`（スキルの説明と使用条件）を記述し、その下に実際の手順やガイダンスを書く構成です[16]。説明文には「このスキルが何をするか」「どんな場合に使うか」を具体的に含め、エージェントがトリガー判断しやすいキーワードを入れることが推奨されています[17]。

SKILL.mdのMarkdown本文には形式上の厳格な制限はありませんが、典型的には「使うべき状況」「具体的な手順」「出力フォーマット」「例」「参照リソース」など見出しで区切り、読む人間にも分かりやすく整理します[18]。この自己記述性により、スキルの内容は監査や改善が容易です[19]。

#### scripts/（任意）

スキル専用の実行スクリプトを置くディレクトリです[20]。例えばPythonやShellのスクリプトを置いておけば、エージェント（Claude）はそれを直接実行して結果を得られます[21]。モデルにコードを書かせるより効率的・確実な処理（重い計算、データ変換、外部API呼び出し等）はスクリプトに任せることで一貫した決定的な結果を得られ、コンテキストも節約できます[22]。実行時、スクリプトの中身自体はコンテキストに入れず出力だけがモデルに渡るため、大量のコードでもコンテキストを圧迫しない利点があります[23]。

#### references/（任意）

マニュアルや詳細ドキュメントなど、必要時に読み込む参照資料を置くディレクトリです[20]。SKILL.mdからハイパーリンクで参照することで、エージェントは該当箇所が必要になったときにそのファイルを開いて内容を読み込みます[12]。例えばPDF操作スキルでは、フォーム入力に関する詳細手順を`forms.md`に分離し、SKILL.md本文からリンクしました。普段は読まずに済む情報を分離することでスキル本体をコンパクトに保ち、必要な場面でだけ追加読み込みするよう工夫しています[12]。

#### assets/（任意）

テンプレートや定型フォーマット、画像・HTMLなどリソースファイルを置くディレクトリです[24]。出力書式の雛形（例えばレポート用HTMLテンプレート）や、参照用の画像・スプレッドシート等を含め、エージェントが必要に応じてそれらのファイルを読むか利用します。これもreferences同様、あくまで必要になったときだけ読み込まれるため、コンテキスト負荷なく豊富な資料を持たせておけます[25]。

---

このようにAgent Skillsは、新入社員に渡すオンボーディング手引きを用意する感覚でエージェントに追加知識を与えるものです[26]。しかもその手引きはフォルダ構成で整理され、内容量に制限なく詰め込める（使わなければコンテキストを消費しないため）点で非常に柔軟です[27]。Anthropicは「Progressive Disclosure（段階的開示）こそSkillsを柔軟かつスケーラブルにする設計上の肝」であると述べています[28]。この設計により、実質無制限の知識ベースを持ちながら俊敏に必要部分だけを利用するスマートなエージェント運用が可能になります[29]。

---

## Skills作成・展開の推奨プロセス

効果的なAgent Skillを作成するには、場当たりで大量の情報を書き込むのではなく計画的かつ段階的に構築・検証することが重要です[30]。以下に推奨される手順をステップごとにまとめます。

### 1. ニーズとギャップの特定（Evaluation）

まず、エージェントに何が足りていないかを洗い出します。代表的なタスクを試行し、どの局面でエージェントが失敗するか、追加の知識や手順が必要かを観察します[30]。例えば「社内の特定フォーマットのレポート作成が苦手」「大容量ファイルを扱うときに誤る」等、スキルで強化すべきポイントを明確にします。

### 2. スキルの範囲設定と設計

解決したい問題が定まったら、そのためのスキルの役割と範囲を定義します。目的が広すぎる場合は分割も検討し、一つのスキルは一連の関連するタスクに集中させます（例: 「PDF編集」スキルと「データベース問い合わせ」スキルを別々に作る）。次にスキル名と説明文（`name`と`description`）を考案します。説明文は先述の通り「何をするか」「いつ使うか」を具体的に書き、エージェントがそのスキルをどの状況で発動すべきか判断しやすい表現にします[31]。ここで抽出したキーワードがスキルのトリガーになります。

### 3. 雛形の利用と構成準備

Anthropicは公式リポジトリでテンプレート（ひな型）を提供しており、新規スキル作成時にはこれを利用すると効率的です[32]（例えば`anthropics/skills`の`template`フォルダを参照）。また、Anthropic提供の「skill-creator」というメタスキルもあります。これは新しいスキルのひな型ディレクトリを自動生成するスキルで、指定したパスにSKILL.mdと基本ディレクトリ構成（scripts, references, assets各フォルダとサンプルファイル）を作ってくれます[33]。Cursorユーザからも、手作業よりこのスキルを使って雛形を作る方法が推奨されています[33]。ひな型を用いることで書式漏れや初期設定ミスを防ぎ、共通の構成でスキル開発を始められます。

### 4. SKILL.md本文の作成

SKILL.mdの中身を書く段階では、エージェントへの具体的な指示手順をMarkdown形式で記述します。ベストプラクティスとして箇条書きの手順やセクション見出しを使い、情報を体系立てて整理することが重要です[34]。例えば以下のような構成が推奨されます[35]：

- **概要/目的**: スキルの目的や適用範囲を1-2文でまとめる
- **前提条件**: 必要なツールや入力、前提知識があれば記載
- **手順**: ステップ1、ステップ2…のように順序立てた命令文で具体的な操作方法を書く（エージェントへの命令は「～せよ」のような命令形で簡潔にし、「～してはどうでしょう？」のような婉曲表現や主観的助言は避ける）
- **出力形式**: エージェントが生成すべきアウトプットの形式やフォーマットを明示する
- **エラーハンドリング**: 失敗時にどう対処すべきか（リトライ、ユーザへの確認、ログ保存等）指示する
- **具体例**: 想定されるユーザー入力や状況に対し、このスキルならどう対処するかの例を示す
- **参照リソース**: scriptsやreferences内のファイルに触れる場合は、その使用法や内容概要を説明しておく

ポイントは、長大な説明文を詰め込みすぎないことです。SKILL.mdはあくまでコアの手順に留め、詳細なチュートリアルやデータは別ファイルに分離します[36]。目安として全文で5,000語程度（数千トークン）以下に抑えると、エージェントが一度に理解しやすくなります[36]。またエージェントはSKILL.md読み込み時にそのスキル専用の作業ディレクトリパス（`{baseDir}`変数）を把握しています[37]。従って参照や実行コマンドでは絶対パスではなく`{baseDir}`プレフィックスを用いるのが望ましく、これによりスキルをどの環境に置いてもパスを書き換える必要がなくなります[38]。

### 5. 補助ファイル・コードの活用

手順を書く中で、明細な内容（例えばAPIの詳細仕様や大量のサンプル）を載せたくなったら、それは無理にSKILL.mdに埋め込まずreferencesフォルダのMarkdownに切り出してリンクしましょう[12]。必要時にのみ開かれるため、スキルの応答スピードや文脈を圧迫しません。同様に、複雑な処理や正確さが求められる作業（例えば数値計算、テキストフォーマット変換、外部サービス呼び出しなど）はscriptsフォルダにコードを書いて対応します[22]。エージェントはSKILL.md内の指示文に基づき適切なタイミングでそのスクリプトを実行し、結果だけを取り込みます[23]。これにより手順の一貫性と結果の再現性が高まります[22]。AnthropicのPDF処理スキルでも、フォームフィールド抽出用にPythonスクリプトを同梱し、Claudeはそれを実行してフォーム項目一覧を得る仕組みになっています[22]。

### 6. テストと検証

スキルが一通り完成したら、想定するユーザタスクにエージェントを実行させ、スキルが正しくトリガーされ動作するか確認します。例えば新規作成したスキルの`description`に「PDF」や「フォーム」というキーワードを入れたなら、ユーザに「このPDFフォームに入力して」と依頼させ、Claudeが自動でPDFスキルを読み込むかを見るわけです。Anthropicはスキル開発時には評価視点を持つことを強調しており、代表的なユースケースでエージェントがなお誤るなら追加の知識を入れる、といったインクリメンタルな改良を推奨しています[30]。

- **スキルが発動しない場合**: `description`に含めたキーワードや表現が適切か見直します。必要ならより明示的な言葉に変えたり、別の関連語も含めます[31]。またエージェントがスキルの存在を認識しているか（メタデータ読み込み済みか）環境設定を確認します。
- **スキルは発動したが期待通り動かない場合**: Claudeの出力内容や、実際にSkill内で読まれたファイル・実行されたコマンドをログから追跡します（Claudeではシステムプロンプトや実行ツールの痕跡が確認できます）。予期せぬ手順を踏んでいないか、必要なファイルを読んでいるかチェックし、不足している説明や過剰な説明がないか調整します[39]。

### 7. 反復改善（Iterate with AI）

Anthropicはスキル作成においてClaude自身と対話しながら進めることを勧めています[40]。具体的には、スキル適用中のClaudeに「今うまくいったアプローチをスキルに追加しよう」「失敗した原因を自己分析してみて」と促すことで、モデルから見た有用なコンテキストが得られます[40]。Claudeが自ら提案した補足情報や修正点を取り入れる形でスキル内容を洗練させると、机上で人間が想定するより実運用に即した知識集約が可能になります[40]。こうしたモデルとの対話による反復により、必要十分で過不足ないスキル内容へと近づけます。

### 8. スキルの導入・デプロイ

スキルが完成しテストも通ったら、実際の環境にインストール/配置します。Anthropic Claude製品群の場合、利用方法はいくつかあります：

- **Claude.ai (Web版)**: Pro版以上では、設定画面からZIP圧縮したスキルフォルダをアップロードできます[40]。アップロード後は自動的に有効化され、ユーザのプロンプトに応じてClaudeが使用します[40]。
- **Claude Code (IDE)**: Claude Codeではファイルシステム上に配置されたスキルフォルダを自動認識します[40]。プロジェクト内の適切な場所（デフォルトではワークスペース直下か指定のskillsフォルダ）にスキルフォルダを置けば、再起動後にClaude Codeがそれらを検出し、以降の対話で使用可能になります[41]。
- **Claude API**: API経由ではスキル用のエンドポイント（`/v1/skills`）でスキルをアップロードし、対話実行時にリクエストの`container`パラメータで利用したいスキルIDを指定します[42]。Claude APIでスキルを使うには事前にコード実行コンテナ等を有効化する必要があります（2025年現在Beta機能）[43]。
- **Claude Agent SDK**: Claudeを組み込んだ独自アプリ開発向けSDKでもスキルをサポートしています。`.claude/skills/`ディレクトリにスキルを配置し、SDKの設定でSkill機能を有効にする（allowed_toolsに`Skill`を含める）と、自動的に検出・使用されます[44]。

Anthropic以外のプラットフォームでも、Agent Skills標準に対応したツールでは似たような導入方法になります。例えばCursor（AI統合開発環境）もAgent Skillsをサポートしており[3]、Cursorの設定でスキル機能を有効化した上で所定のフォルダにスキルを置くことで、Cursor内のエージェントがそれらを使用できるようになります。具体的にはCursor 2.3.5以降のナイトリービルドでスキル機能が試験導入されており、ユーザ環境のホームディレクトリ下に`.claude/skills/`フォルダが存在すると設定メニューにスキル関連オプションが現れる、といった報告があります[45]。プロジェクト単位の「Cursorルール（CLAUDE.md）」を作成すると自動で対応するSKILL.mdファイルが生成されるアップデートも確認されています（Cursor v2.3.8）[46]。したがってCursor利用時は`.claude/skills`または`.cursor/skills`配下にスキルを配置し（最新版ドキュメントを確認推奨）、必要に応じてNightlyチャネル等スキル対応版へアップデートすることが必要です[47]。VS Codeの場合も、`.github/skills/`ディレクトリにスキルを置き、設定でAgent Skills機能をオンにすることでCopilotエージェントがスキルを自動利用します[48]。

### 9. スキルのバージョン管理と共有

作成したスキルはGitなどでバージョン管理し、変更履歴やバージョン番号を明確にしておくことが推奨されます[49]（YAMLの`metadata`フィールドに`version`番号を記載しておくとドキュメント上も分かりやすいです[50]）。Anthropicはスキルをオープンなエコシステムで共有することを目指しており、実際公式の公開リポジトリに様々なサンプルスキル集を掲載しています[51]。自作スキルも必要に応じて社内外のリポジトリで共有し、コラボレーションやフィードバックを得ることで品質向上が図れます。特に企業内利用では、組織ポリシーに沿ったスキルのみを共有・展開し、不適切な変更が加えられないようにする管理プロセス（レビューや承認フロー）を設けると安心です[52]。

以上のステップを踏むことで、漫然とプロンプトを書くより体系立ったスキル開発が可能となり、結果としてエージェントの賢さ・信頼性・汎用性を飛躍的に高めることができます。

---

## 再利用性・メンテナンス性の高いスキル設計の原則

Agent Skillsは「一度作って何度も使う」ことを前提とした技術です。その利点を最大化するため、スキル設計において以下の原則を念頭に置くと良いでしょう。

### モジュール性と単一責任

スキルは用途ごとにフォルダを分けるため、一つのスキルは一つの目的にフォーカスさせます。こうすることで必要な時にだけ読み込まれ、不要な知識が他のタスクに干渉しません[53]。汎用的すぎるスキルより、小回りの利く専門スキルを複数組み合わせる方が管理しやすく、後々の改良も局所的に行えます[54]。

### ポータビリティ（移植性）

スキルはAnthropic ClaudeだけでなくOpenAI CodexやGitHub Copilot等でも動作可能です[4]。そのため特定プラットフォーム固有の表現や依存は極力避け、共通フォーマットで記述します。Anthropicでは`.claude/skills`を標準パスとしていますが、GitHubは`.github/skills`を推奨するなど差異もあります[48]。複数環境で使う場合はドキュメントの`compatibility`欄に「Claude専用」「要インターネットアクセス」等条件を明記し[55]、想定外の環境でロードされるのを防ぎます。

### 自己文書化と可読性

スキル内容は人間にとっても読みやすくあるべきです[19]。第三者がSKILL.mdを開いたとき、目的・使い方・手順が追いやすいよう段落やリストで整理し、専門用語には注釈や例示を加えます。エージェントへの指示文を書く際は「開発者視点」ではなく「エージェント視点」で過不足を検討します[39]。一度作って終わりではなく、運用中に人間やAIから指摘があれば都度改善しやすい構成にしておくことが、メンテナンス性向上につながります。

### 命令形で明確に

プロンプトエンジニアリングの基本ですが、スキル内の指示は曖昧さを排除した命令形で記述します[56]。例えば「あなたは～すべきです」より「～せよ」「～を実行」とする方がモデルは解釈しやすく、一貫した動作を引き出しやすいです。特に長い手順を書く際は各ステップを番号付き箇条書き（Step 1, Step 2, …）にして、モデルが順序を間違えないようにします[57]。

### コンテキスト分離と参照

Progressive Disclosureの利点を活かし、一度に使う情報量は必要最小限に抑えます。SKILL.mdが肥大化してきたら、互いに排他的な内容や使用頻度の低い詳細は参照ファイルに退避させます[58]。また逆に複数スキル間で共通の説明がある場合は、重複記載せず一方にまとめるか、あるいは共通のリファレンスを用意して双方から参照する方法も検討します。重複を減らすことが保守性向上の鍵です。

### コードの活用とドキュメント

スキル内でスクリプトを使う場合、そのコード自体もドキュメントの一部と捉えます[59]。コードは最も正確な手順書にもなり得るため、SKILL.md内でその目的や使い方を説明しておきます[60]。またコードをエージェントが読むべきか実行すべきかを区別できるよう記述に工夫します[58]。例えば「以下のスクリプトを実行して結果を得よ」と明示したり、逆にコードを参考情報として見せたい場合はその旨を書き添えます。こうすることでモデルが意図を誤解せず動作します。

### ツール権限の最小化

Agent SkillsのYAMLには`allowed-tools`という項目があり、そのスキルが利用してよいツール（コマンド種別）をホワイトリスト形式で指定できます[61]。これはまだ実験的機能ですが、対応するエージェント環境ではスキル毎に不要なツールアクセスを制限でき、誤用やセキュリティリスクを下げられます。例えばファイル読み書きと特定のコマンドしか必要ないスキルなら、`allowed-tools: "Read,Write,Bash(git:*),…"`のように宣言しておくと安心です（逆に広範に`Bash`を許可するとスキルから予期せぬ操作をされる可能性があるので注意）。このように必要最小限の権限でスキルを動かすのがベストプラクティスです。

### メタデータの活用

YAMLフロントマターの`metadata`フィールドには自由記述で作者名やバージョン、カテゴリ等を記載できます[50]。組織内でスキルを管理する際に、誰がいつ作成/更新したか、どのバージョンをデプロイ中かといった情報をここに持たせておくと便利です。将来的にスキルカタログを社内ポータルなどで整備する場合も、このメタデータを機械的に読み取れば一覧が作成できます。Anthropic自身も企業向けにスキル管理ツールを発表しており、組織全体でのスキルの可視化・ガバナンスに力を入れています[52]。

### 常にテストとフィードバック

スキルは環境や要求が変わると効果も変動します。特に運用開始後は、定期的にエージェントの振る舞いをモニタリングし、スキルが適切に使われているかチェックすることが重要です[39]。もしエージェントがスキルを発動すべき状況で発動しなかったり、逆に不要な場面で発動してしまう場合は、早めに内容を修正します。またモデルがバージョンアップした際にもスキルの挙動が変わり得るので、重要スキルはその都度検証し、アップデートが必要か評価してください。

以上の原則を守ることで、作ったスキルが長期間にわたり使い回せる資産となり、追加要件にも柔軟に対応できるでしょう。「一度作って終わり」ではなく継続的に改善・拡張していく前提でスキル開発を行うことが肝要です[39]。

---

## Cursor統合時の留意点

質問にある通り、多くのケースでAIコンサルタントはCursorなどAI統合開発環境と組み合わせてAgent Skillsを活用することが想定されます。Cursorは既にAnthropicのスキル標準に対応を表明しており[3]、2025年末時点でナイトリーチャンネルにおいてスキル機能を試験実装しています[62]。以下にCursorでSkillsを利用する際のポイントをまとめます。

### 対応バージョンの確認

Cursorの安定版リリースではまだスキル機能が正式公開されていない場合があります（2025年末時点ではBeta/Nightlyのみ対応報告あり[62]）。まず最新バージョンのCursorを用意し、設定で「Agent Skills（スキル）」オプションを有効化できるか確認してください[47]。オプションが見当たらない場合はナイトリービルドへの切替やアップデートが必要です。

### スキルファイルの配置場所

Anthropic準拠の実装ではホームディレクトリ下に`.claude/skills/`フォルダを作り、その中にスキルフォルダを置く運用が基本です[45]。Cursorでもユーザ報告によれば`.claude/skills`以下を参照している可能性が高いです[45]（将来的に`.cursor/skills`に変わる可能性もありますがドキュメント要確認）。いずれにせよCursorのルール/スキル設定に従い、適切なディレクトリにSKILL.mdを配置してください。プロジェクトごとにスキルを限定したい場合、プロジェクト設定内の「Rules」タブから追加することで所定フォルダにSKILL.mdが生成されるようなアップデートも報告されています[46]。

### Cursorルールとの違い

Cursorには従来、プロジェクトごとに動作ルールを記述する`CLAUDE.md`（いわゆる「Cursorルール」）機能がありました。Agent Skillsはこの仕組みに似ていますが、本質的な違いは「常時ロード」か「必要時ロード」かという点です[63]。従来のCLAUDE.mdは内容がセッション毎に常に読み込まれるため、コンテキストを圧迫しうるものでした[63]。一方Skillsは上述の通り必要になるまで詳細内容は読み込まれないため、より多くの知識を持たせてもエージェントの応答速度や他タスクへの影響が少ないという利点があります[9]。実際、Cursor開発者コミュニティでも「Claude SkillsはCursorルールを発展させたようなもので、文脈管理が洗練されている」という指摘があります[64]。したがってCursorでも今後はRulesよりSkillsへ移行する流れが予想されます。既存のCLAUDE.mdで書かれていたガイドラインは、適宜Agent Skills形式（SKILL.md）に移しておくと将来的な互換性で有利でしょう。

### 統合動作

CursorでAgent Skillsが有効な場合、基本的な動作はClaudeと同様です。すなわちCursor内AIアシスタントのシステムプロンプトに全スキルのnameとdescriptionが自動挿入され、ユーザとの対話中に必要と判断すれば裏側でSKILL.mdを開いて読み込みます[14]。例えばユーザが「このコードの単体テストを書いて」と頼んだ際、テスト作成スキルがあればCursorのAIはその説明を見てトリガーし、SKILL.md内のガイドライン（テストのベストプラクティスなど）を読んでからコード提案してくれる、という流れです。ユーザ側で明示的にスキルを呼び出す必要は基本的になく（※）、あくまでエージェントが自律的に判断して使います[65]。（※ただし開発中の一部プラットフォームでは`/skill-name`のようなコマンドで手動呼び出しもできる機能があります。Cursorが対応する場合も想定されます。）

### 複数スキルの組み合わせ

Cursor環境で特筆すべきは、コーディング支援系AIとAgent Skillsのシナジーです。Cursorはコード生成や編集を得意としますが、それを支えるテスト作成スキルやデバッグスキル、ドキュメント生成スキル等を組み合わせることで、より包括的な支援が可能です。スキル間は独立していますが、同一のエージェントが複数スキルを状況に応じて組み合わせて使えるため、Cursor利用者は必要なドメイン知識をそれぞれスキル化しておくことで大幅な作業自動化・効率化を実現できます[66]。

### 互換性維持

前述の通りAgent Skills標準に準拠したスキルはCursorだけでなく他ツールでも使えます。開発現場ではCursor（Claudeモデル使用）を使い、運用では例えばGitHub Copilot CLI（OpenAIモデル使用）に同じスキルを載せる、といったマルチエージェント環境で一貫した知識を共有することも可能です[4]。その際、互換性に問題が起きやすいポイント（外部コマンドの有無、モデルの違いによる表現ゆらぎなど）に注意してスキルを調整します。例えばAnthropic Claude専用のスキルをOpenAI GPT-4ベースの環境でも使うなら、OpenAI側に存在しないツール呼び出し（例: Bashツール）が含まれていないかチェックし、必要ならスキル側で代替手段を用意します。

以上を踏まえ、CursorとAgent Skillsを統合活用する際は、最新の対応状況を確認しつつ、スキルの構造を標準に忠実に保つことが成功のポイントです。Cursor自体も進化中のため、今後設定方法やサポート範囲が変わる可能性がありますが、根底にある「スキルをフォルダ構成で管理し必要時ロードする」という思想は共通です。AIコンサルタントとしては、Cursor利用者に対してこの標準に沿ったスキル開発を提案し、将来的なプラットフォーム移行や統合にも耐えうるソリューションを提供できると理想的です。

---

## 実例紹介：スキル構成と記述例

実際のAgent Skillsの例をいくつか挙げ、その構成や記述パターンを紹介します。

### PDF処理スキル（PDF Processing Skill）

AnthropicがClaudeのドキュメント編集機能の裏で使用しているスキルの一つがPDF操作用スキルです[67]。このスキルでは、PDFからテキストや表を抽出したり、フォームに入力したり、複数PDFを結合するといった操作手順がまとめられています[67]。

構成を見ると、`SKILL.md`では「いつこのスキルを使うか」（ユーザがPDFについて言及したとき等）と「各操作の手順」（テキスト抽出の方法、フォーム入力方法…）が段落分けされて記述され、詳細なフォーム入力手順は`forms.md`という別ファイルに委譲されています[12]。また、フォーム抽出には`fill_form.py`というPythonスクリプトが`scripts/`に含まれており、Claudeは必要に応じこれを実行してフォームフィールド一覧を取得します[22]。この例から、中心的な手順（抽出・結合など）はSKILL.mdに、細かなケース別手順（フォーム記入など）は参照ファイルに、プログラム実行が適した処理（フォームフィールド取得など）はスクリプトにと適材適所で配置している点が分かります。結果としてClaudeは「PDF関連作業」が要求された時だけこのスキルを呼び出し、しかも必要な部分だけ読み込んで効率良く対処できています[68]。

### オフィス文書スキル

Anthropicは一般利用者向けにPowerPoint, Excel, Word, PDFのドキュメント操作スキルをあらかじめ提供しています[42]。例えばExcel（`xlsx`）スキルでは、「表計算データを解析・変換する方法」「特定セルを参照・更新する方法」などが含まれており、Pandasの使用例コードや注意点が記載されています。これらはClaude.ai上ではデフォルトで有効になっており、ユーザが「このExcelファイルを集計して」と依頼すると自動で発動します[42]。事前構築されたスキルを活用できる場面ではゼロから作らずとも既存スキルを導入するだけで高機能化できるため、まず公開されているスキルを調査するのも良いでしょう。Anthropicの公開リポジトリ（`anthropics/skills`）には企業向け文書作成、デザイン、コーディング支援、コミュニケーションなど多彩なカテゴリのスキル例が収録されており[51]、自作の際の参考になります。

### Skillテンプレート（InfoSecポリシードラフトの例）

スキルの記述スタイルの一例として、あるブログで紹介されている情報セキュリティポリシー策定スキルのテンプレートを抜粋します[69]：

- **Purpose（目的）**: 情報セキュリティポリシーをISO27001に沿ってドラフト/更新し、レビュー可能な成果物を作成すること
- **Triggers（トリガー）**: 「Infosecポリシーをドラフトして…」「手順書を作成して…」等（※descriptionに相当）
- **Inputs（入力）**: ポリシーのテーマ・範囲・対象、制約条件（適用基準や法規）、参照すべき既存文書 等
- **Steps（手順）**: 1) 範囲の明確化（曖昧な点は質問で確認）2) セクションのアウトラインを作成（5つの箇条書きで提示）3) 見出しや定義、RACIを含めドラフト作成 4) ISO管理策との対応付けと変更履歴追加 5) 要約と次のアクション作成[70]
- **Outputs（出力）**: ポリシー文書（Markdown形式、見出しと番号付き節を含む）、経営層向け200語サマリー、レビュー用チェックリスト[71]
- **Refusal & Escalation（拒否とエスカレーション）**: 法的アドバイスは拒否。規制に関する判断は法務または専門家に委ねる警告を出す[72]。
- **Evaluation Checklist（評価チェックリスト）**: 事実関係が提供ソースに沿っているか、ISO対応付けが含まれているか、読みやすさ（中学生程度の読解レベル）になっているか…などの完了基準[73]。

この例は非技術ドメインのスキルですが、手順指示・出力物・注意事項・評価基準まで盛り込んでおり、エージェントがかなり高度なアウトプットを自律的に作れるよう工夫されています[74]。実務ではここまで詳細に書かなくとも良い場合もありますが、コンプライアンスや品質基準が厳しいタスクでは、このようにチェックリストや拒否基準まで含めておくと安全に使えるスキルになります。AIコンサルタントはクライアント業務の性質に応じ、必要ならこの例のようなガードレール（脱線防止策）もスキル内に組み込むことを提案すると良いでしょう。

### コードレビュー/テストスキル

技術領域では、例えば「コードレビューの観点」をまとめたスキルや、「単体テストの方針」を示すスキルが考えられます。VS Code用ドキュメントではWebアプリテストのスキル例が挙げられており、SKILL.mdにテスト実行手順が、付随ファイルにテストコードのテンプレート`test-template.js`やシナリオ例が含まれる構成になっています[75]。このようにコードの規約や典型パターンをテンプレート化しておけば、エージェントはそれに沿ったコードやテストを自動生成できます。特に複数プロジェクトで共通するベストプラクティス（コーディング規約やテスト観点など）はスキル化して共有しておくと、チーム全体の生産性とコード品質の底上げに役立ちます。

これらの実例から分かるように、Agent Skillsの形式は柔軟であり文章・手順・コード・チェックリストなどあらゆる要素を組み合わせてエージェント行動をデザインできることが特徴です。テンプレートを活用しつつ自社やプロジェクトに合わせてカスタマイズすることで、汎用LLMをドメインに最適化したエージェントへと育て上げることができるでしょう。

---

## セキュリティ・信頼性・運用上の留意点

最後に、Agent Skillsを実務で活用する際に注意すべきセキュリティや運用面のポイントを整理します。スキルはエージェントに強力な新機能を与える反面、その内容次第で思わぬリスクを招く可能性もあります。Anthropicも公式に以下の点を注意喚起しています[76]。

### 信頼できるソースからのみ導入

スキルそのものが悪意を持って作られていた場合、エージェントに危険な振る舞いをさせる恐れがあります[77]。例えば不正なスキルがあれば、エージェントにシステム上で有害なコマンドを実行させたり、内部情報を外部に送信させたりするかもしれません[77]。従って公開スキルを利用する際は出所を確認し、Anthropic公式や信頼できるコミュニティ提供のもの以外は注意深く検証しましょう。組織内で独自スキルを導入する場合も、社内でコードレビューを経てから展開するなどガバナンスを効かせることが重要です。

### スキル内容の監査

新しいスキルをインストールまたは自作した際は、まずSKILL.mdおよび含まれる全ファイルの内容を人間が精査してください[76]。特にスクリプトコード（PythonやShell等）は何をしているか読み、依存ライブラリや外部サービスへのアクセス先をチェックします[76]。同様に、SKILL.md内の記述でエージェントにネットワーク越しのリソース取得や外部API呼び出しを指示していないか確認します[78]。もしそうした箇所がある場合、そのアクセス先が安全か（マルウェアやデータ流出のリスクはないか）を検討し、必要ならファイアウォール設定やネットワーク隔離など技術的対策も講じましょう。

### 権限と範囲の制御

前述の`allowed-tools`フィールドの活用もセキュリティ強化に有効です。エージェントがそのスキル実行中に使えるコマンドを限定すれば、仮にスキルが乗っ取られても被害を抑えられます。またエージェントプラットフォーム側で用意されている組織ポリシー機能（Anthropicのエンタープライズ機能など[52]）があれば、組織として許可するスキルや使用禁止のスキルを制御することも可能です。特定のスキルのみホワイトリストに載せ、それ以外は読み込まれないように設定することで、不用意なスキル追加によるリスクを防げます。

### 機密情報の扱い

スキルには社内ノウハウや機密データも書き込まれる可能性があります。基本的にモデルに与えたテキストはその出力に現れる可能性がある（例えばユーザが巧妙なプロンプトを与えるとスキル内容を引き出せてしまう）ことに注意してください。極端な例ですが、SKILL.mdにハードコードでパスワードや秘密鍵などを書いてしまうのは厳禁です。同様に公開リポジトリで管理するスキルには社外秘情報を含めないよう留意が必要です。どうしても機密情報を参照する必要がある場合は、スキル実行時に安全にアクセスできる社内API経由で取得する、など別の方法を検討します。

### スクリプトの安全性

スキル内スクリプトを実行する場合、その実行環境のセキュリティにも配慮します。Anthropic Claudeの場合、エージェントにはサンドボックス化された仮想環境が用意されるため、システム自体への影響は限定的とされています[79]。しかし完全ではない可能性もあるため、スクリプトがファイルシステムやネットワークへ及ぼす操作範囲には気を配りましょう。ファイル削除や外部送信を行うコードは極力避け、必要ならユーザ確認を求めるなどフェイルセーフを入れてください。スクリプト実行結果もエージェントはそのまま信用しますので、結果のバリデーション（想定フォーマットか、異常値が混じっていないか等）を行わせるチェック手順を組み込むのも一案です。

### スキルのテストとモニタリング

本番運用前にスキルの動作検証を徹底するのはもちろんですが、運用中も定期的なモニタリングが望まれます[39]。特にエージェントが誤った場面でスキルを使っていないか（または使うべき場面で見逃していないか）や、スキルを使った結果おかしな応答を返していないか、といった点をログ解析やユーザフィードバックで追跡します。Anthropicのエンタープライズ管理ツールではスキル利用状況の集計や制限が可能とされています[52]。そうした機能がない場合も、重要な出力には人間のレビュー工程をはさむなどHuman in the Loopを維持しておくと安心です。

### スキルの更新と互換性

スキルをアップデートする際、新旧バージョンでエージェントの挙動差異が出る可能性があります。可能であればスキルにバージョニングを行い（v1, v2など）、エージェントに新旧を混在させず一斉切替するか、互換性維持のため古い記述も残して徐々に移行するか、ポリシーを決めておきます。組織内で複数スキルを運用する場合、それらの相互作用にも留意し、片方を変えたら他方のトリガー条件と競合しないか等チェックしましょう。またオープン標準とはいえプラットフォーム実装依存の部分もあるため、AnthropicやCursorなど使用エージェントのアップデート情報（スキル仕様変更等）も定期的にウォッチし、それに追随する形でスキルもメンテしていく姿勢が大事です。

以上の点を踏まえれば、Agent Skillsは適切なガバナンス下で安全かつ強力な社内AI能力強化ツールとなります。特にセキュリティや品質が重要視される業務領域では、スキルを導入しっぱなしにせず継続的な監査・改善サイクルを設けることが成功の秘訣と言えるでしょう[40][76]。

---

## おわりに: AIコンサルタントによるスキル活用の展望

Anthropic発のAgent Skillsは、AIエージェント開発における知識の再利用性とコンテキスト制御という難題に対するエレガントな解決策です。そのオープン標準化により、ベンダーロックインを避けつつ業界全体でノウハウを共有できる基盤が整いつつあります[4][80]。AIコンサルタントにとって、この技術を正しく理解し活用することは、クライアントの業務分析から導入・展開まで一貫して質の高いAIソリューションを提供する鍵となるでしょう。

本調査で述べたベストプラクティスをまとめると、以下のようになります。

- スキル開発前に目的と必要知識を明確化し、段階的に構築すること（性急に大量のプロンプトを詰め込まず、評価→設計→実装→テスト→改善のプロセスを踏む）[30][40]。
- Progressive Disclosureの思想を活かし、スキルを軽量・適時にロードされる形で設計すること（name/descriptionを的確に書き、詳細は参照ファイルやコードに委ねる）[9][12]。
- エージェント視点で書かれた明確な手順書を用意し、必要に応じてコードやテンプレートを組み合わせて信頼性と効率を高めること[22][81]。
- スキルを汎用化・モジュール化しておくことで複数環境で再利用し、組織の知的資産として維持管理すること[4][51]。
- Cursor等ツールとの統合では最新情報をチェックし、標準に従った配置と設定でスキルを最大限に活かすこと[3][45]。
- セキュリティと品質管理を怠らず、スキル導入前後の監査や使用状況のモニタリング、定期メンテナンスを行うこと[76][52]。

Agent Skillsは「フォルダとファイル」というシンプルな形でエージェントに知恵を与える強力な仕組みです[82]。AIコンサルタントとして、この仕組みを活用すれば、従来は個別にプロンプトを調整していたようなケースでも再利用可能なスキルセットを構築して汎用モデルに専門性を持たせることができます。例えば業務改善の文脈では、社内標準手順をスキル化して新人社員のようなAIアシスタントを作る、といったシナリオも現実味を帯びてきます[83]。最終的には、Anthropicが示唆するようにエージェント自身がスキルを学習・改良していく未来も展望されています[84]。現時点では人間が主導してスキルを設計しますが、本ガイドラインに沿って良質なスキルを蓄積しておくことが、そのような次世代の自己学習型AIへの橋渡しになるかもしれません。

以上、Anthropic提唱のAgent Skillsに関するドキュメント、コミュニティ情報、実装例などを総合し、スキル作成のベストプラクティスを整理しました。これからAgent Skillsを活用するAIコンサルタントの皆様の業務に、本資料が少しでも役立てば幸いです。

---

## 引用元一覧

1. Overview - Agent Skills - https://agentskills.io/home
2. Anthropic Opens Agent Skills Standard, Continuing Its Pattern of Building Industry Infrastructure – Unite.AI - https://www.unite.ai/anthropic-opens-agent-skills-standard-continuing-its-pattern-of-building-industry-infrastructure/
3. What are skills? - Agent Skills - https://agentskills.io/what-are-skills
4. Agent Skills - Claude Docs - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
5. Equipping agents for the real world with Agent Skills | Anthropic - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
6. Specification - Agent Skills - https://agentskills.io/specification
7. Claude Agent Skills: A First Principles Deep Dive - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
8. I Built a Claude Skill in Under 30 Minutes And It Immediately... | Medium - https://medium.com/@PowerUpSkills/i-built-a-claude-skill-in-under-30-minutes-and-it-immediately-elevated-myworkflow-084a10d95338
9. Claude Agent Skills: A First Principles Deep Dive（Progressive Disclosure） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
10. Claude Agent Skills: A First Principles Deep Dive（コンテキスト制約） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
11. Specification - Agent Skills（メタデータ読み込み） - https://agentskills.io/specification
12. Equipping agents for the real world with Agent Skills | Anthropic（参照ファイル分離） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
13. Claude Skills and CLAUDE.md: a practical 2026 guide for teams - https://www.gend.co/blog/claude-skills-claude-md-guide
14. Claude Agent Skills: A First Principles Deep Dive（必要時ロード） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
15. Specification - Agent Skills（スキル構成） - https://agentskills.io/specification
16. Specification - Agent Skills（YAMLフロントマター） - https://agentskills.io/specification
17. Specification - Agent Skills（description記述） - https://agentskills.io/specification
18. Claude Agent Skills: A First Principles Deep Dive（SKILL.md構成） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
19. Claude Agent Skills: A First Principles Deep Dive（自己記述性） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
20. Specification - Agent Skills（scripts/references） - https://agentskills.io/specification
21. Specification - Agent Skills（スクリプト実行） - https://agentskills.io/specification
22. Equipping agents for the real world with Agent Skills | Anthropic（スクリプト活用） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
23. Equipping agents for the real world with Agent Skills | Anthropic（コンテキスト節約） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
24. Specification - Agent Skills（assets） - https://agentskills.io/specification
25. Claude Agent Skills: A First Principles Deep Dive（assets活用） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
26. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance - https://lethain.com/agents-skills/
27. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance（柔軟性） - https://lethain.com/agents-skills/
28. Claude Agent Skills: A First Principles Deep Dive（Progressive Disclosure重要性） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
29. Equipping agents for the real world with Agent Skills | Anthropic（スケーラビリティ） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
30. Equipping agents for the real world with Agent Skills | Anthropic（評価プロセス） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
31. Specification - Agent Skills（キーワード設計） - https://agentskills.io/specification
32. GitHub - anthropics/skills: Public repository for Agent Skills - https://github.com/anthropics/skills
33. I Built a Claude Skill in Under 30 Minutes And It Immediately... | Medium（skill-creator） - https://medium.com/@PowerUpSkills/i-built-a-claude-skill-in-under-30-minutes-and-it-immediately-elevated-myworkflow-084a10d95338
34. Claude Agent Skills: A First Principles Deep Dive（SKILL.md作成） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
35. Claude Agent Skills: A First Principles Deep Dive（推奨構成） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
36. Claude Agent Skills: A First Principles Deep Dive（文字数目安） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
37. Equipping agents for the real world with Agent Skills | Anthropic（baseDir変数） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
38. Equipping agents for the real world with Agent Skills | Anthropic（パス設計） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
39. Equipping agents for the real world with Agent Skills | Anthropic（テスト・モニタリング） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
40. Equipping agents for the real world with Agent Skills | Anthropic（反復改善・デプロイ） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
41. Agent Skills - Claude Docs（Claude Code） - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
42. Agent Skills - Claude Docs（Claude API） - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
43. Agent Skills - Claude Docs（Beta機能） - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
44. Agent Skills - Claude Docs（SDK） - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
45. IS Skills Supported - Help - Cursor - Community Forum - https://forum.cursor.com/t/is-skills-supported/146837
46. IS Skills Supported - Help - Cursor - Community Forum（v2.3.8） - https://forum.cursor.com/t/is-skills-supported/146837
47. IS Skills Supported - Help - Cursor - Community Forum（設定確認） - https://forum.cursor.com/t/is-skills-supported/146837
48. Use Agent Skills in VS Code - https://code.visualstudio.com/docs/copilot/customization/agent-skills
49. GitHub - anthropics/skills: Public repository for Agent Skills（バージョン管理） - https://github.com/anthropics/skills
50. Specification - Agent Skills（metadata） - https://agentskills.io/specification
51. GitHub - anthropics/skills: Public repository for Agent Skills（スキル集） - https://github.com/anthropics/skills
52. Equipping agents for the real world with Agent Skills | Anthropic（エンタープライズ管理） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
53. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance（モジュール性） - https://lethain.com/agents-skills/
54. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance（専門スキル） - https://lethain.com/agents-skills/
55. Specification - Agent Skills（compatibility） - https://agentskills.io/specification
56. Claude Agent Skills: A First Principles Deep Dive（命令形） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
57. Claude Agent Skills: A First Principles Deep Dive（番号付き手順） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
58. Claude Agent Skills: A First Principles Deep Dive（コンテキスト分離） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
59. Claude Agent Skills: A First Principles Deep Dive（コードドキュメント） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
60. Claude Agent Skills: A First Principles Deep Dive（コード説明） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
61. Specification - Agent Skills（allowed-tools） - https://agentskills.io/specification
62. IS Skills Supported - Help - Cursor - Community Forum（Nightly対応） - https://forum.cursor.com/t/is-skills-supported/146837
63. Claude Skills are just .cursorrules, change my mind : r/ClaudeAI - https://www.reddit.com/r/ClaudeAI/comments/1oj109n/claude_skills_are_just_cursorrules_change_my_mind/
64. Claude Skills are just .cursorrules, change my mind : r/ClaudeAI（コミュニティ指摘） - https://www.reddit.com/r/ClaudeAI/comments/1oj109n/claude_skills_are_just_cursorrules_change_my_mind/
65. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（自律判断） - https://www.gend.co/blog/claude-skills-claude-md-guide
66. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance（複数スキル組み合わせ） - https://lethain.com/agents-skills/
67. Equipping agents for the real world with Agent Skills | Anthropic（PDF処理スキル） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
68. Equipping agents for the real world with Agent Skills | Anthropic（効率的な処理） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
69. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（InfoSecテンプレート） - https://www.gend.co/blog/claude-skills-claude-md-guide
70. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（Steps） - https://www.gend.co/blog/claude-skills-claude-md-guide
71. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（Outputs） - https://www.gend.co/blog/claude-skills-claude-md-guide
72. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（Refusal） - https://www.gend.co/blog/claude-skills-claude-md-guide
73. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（Evaluation） - https://www.gend.co/blog/claude-skills-claude-md-guide
74. Claude Skills and CLAUDE.md: a practical 2026 guide for teams（高度なアウトプット） - https://www.gend.co/blog/claude-skills-claude-md-guide
75. Use Agent Skills in VS Code（テストスキル例） - https://code.visualstudio.com/docs/copilot/customization/agent-skills
76. Equipping agents for the real world with Agent Skills | Anthropic（セキュリティ注意） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
77. Equipping agents for the real world with Agent Skills | Anthropic（悪意あるスキル） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
78. Equipping agents for the real world with Agent Skills | Anthropic（ネットワークアクセス確認） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
79. Equipping agents for the real world with Agent Skills | Anthropic（サンドボックス） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
80. Anthropic Opens Agent Skills Standard, Continuing Its Pattern of Building Industry Infrastructure – Unite.AI（オープン標準） - https://www.unite.ai/anthropic-opens-agent-skills-standard-continuing-its-pattern-of-building-industry-infrastructure/
81. Claude Agent Skills: A First Principles Deep Dive（手順書設計） - https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
82. Agent Skills - Claude Docs（シンプルな仕組み） - https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
83. Building an internal agent: Adding support for Agent Skills | Irrational Exuberance（業務活用例） - https://lethain.com/agents-skills/
84. Equipping agents for the real world with Agent Skills | Anthropic（将来展望） - https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

---

## 付録：関連ツール・プラットフォーム一覧

### AIエージェントプラットフォーム

| ツール名 | 概要 |
|---------|------|
| **Claude.ai** | AnthropicのWebベースAIアシスタント。Agent Skills対応 |
| **Claude Code** | Claude搭載のIDE/開発環境。スキルフォルダ自動認識 |
| **Claude API** | Claude APIエンドポイント。`/v1/skills`でスキル管理 |
| **Claude Agent SDK** | Claudeを組み込んだアプリ開発向けSDK |

### AI統合開発環境（IDE）

| ツール名 | 概要 |
|---------|------|
| **Cursor** | AI統合開発環境。Agent Skills標準対応（Nightly版） |
| **VS Code** | Microsoft製エディタ。`.github/skills/`でCopilotスキル対応 |
| **GitHub Copilot** | GitHub提供のAIコーディング支援。Agent Skills対応 |

### スキル管理・共有

| ツール名 | 概要 |
|---------|------|
| **anthropics/skills** | Anthropic公式スキルリポジトリ。テンプレート・サンプル集 |
| **skill-creator** | 新規スキルのひな型を自動生成するメタスキル |

### Agent Skills採用企業・プラットフォーム

| 企業/プラットフォーム | 概要 |
|---------|------|
| **Anthropic** | Agent Skills標準の開発元 |
| **Microsoft** | VS Code/GitHub CopilotでAgent Skills対応 |
| **OpenAI** | Codex等でAgent Skills標準採用 |
| **GitHub** | Copilot/リポジトリでAgent Skills対応 |
| **Atlassian** | Agent Skills標準採用を表明 |
| **Figma** | Agent Skills標準採用を表明 |
| **Cursor** | AI IDEでAgent Skills対応 |

### スキル構成ファイル

| ファイル/ディレクトリ | 用途 |
|---------|------|
| **SKILL.md** | スキルの中核。YAMLフロントマター + Markdown手順書 |
| **scripts/** | 実行可能スクリプト（Python, Shell等）を配置 |
| **references/** | 参照用ドキュメント（詳細マニュアル等）を配置 |
| **assets/** | テンプレート、画像、HTMLなどリソースを配置 |

### 関連技術・概念

| 用語 | 概要 |
|---------|------|
| **Progressive Disclosure** | 情報を段階的に開示する設計思想。Agent Skillsの核心 |
| **コンテキストウィンドウ** | モデルが一度に保持できるテキスト量の制約 |
| **allowed-tools** | スキルが使用可能なツールのホワイトリスト（実験的機能） |
| **CLAUDE.md** | Cursorの従来のプロジェクトルール（常時ロード型） |
