# TimeTree Sync Action

TimeTree の予定を Google Calendar へ自動同期する GitHub Actions です。

GitHub Actions を利用して定期的に実行し、TimeTree の予定を Google Calendar に反映します。

## 主な機能

* TimeTree にログイン
* 指定した TimeTree カレンダーの予定を取得
* Google Calendar へ予定を自動同期
* 新しい予定を自動作成
* 変更された予定を自動更新
* 削除は一切行わない（TimeTree 側で削除された予定・既存の重複も保持）
* TimeTree のイベントIDを利用した差分同期
* 同じ予定の重複登録を防止
* Google Calendar で直接作成した予定や、別カレンダーの同期予定には触れない
* GitHub Actions による定期自動実行

---

## 同期の仕組み

TimeTree のイベントIDを利用して、Google Calendar の予定と照合します。

| TimeTree の状態      | Google Calendar での動作 |
| ----------------- | -------------------- |
| 新しい予定             | 作成                   |
| 内容が変更された予定        | 更新                   |
| 変更のない予定           | スキップ                 |
| TimeTree で削除された予定 | 変更せず保持              |

このプログラムが更新するのは、`sync_source=timetree-sync-action` と
現在の `timetree_calendar_code`、`timetree_id` が揃った予定だけです。
所有元が不明な旧形式の予定、同じIDの重複は、上書き・自動移行・削除をせず保持します。
その場合は新しい重複も作らず、ログの `preserve` 件数に含めます。

更新は変更されたタイトル・日時・場所・メモだけを PATCH します。
Google 側の通知、参加者、色、公開設定、添付や独自メタデータは保持します。
ただし、TimeTree と同期する項目を Google 側だけで変更した場合は、次の同期で
TimeTree の内容が優先されます。取得結果に場所・メモのキーが欠けていれば保持し、
明示的な空欄は反映します。更新直前に別の編集が入った場合は ETag により停止します。

Google Calendar で直接作成した予定には TimeTree のイベントIDが付与されないため、同期処理によって削除されることはありません。

> [!TIP]
> TimeTree の同期専用として、新しい Google カレンダーを作成して使用することをおすすめします。
>
> 個人用や仕事用など、すでに使用しているメインカレンダーと分離できるため、予定を管理しやすくなります。

---

## 動作環境

* Python 3.12 以上
* GitHub Actions
* Google Calendar API
* TimeTree アカウント
* Google アカウント

---

# セットアップ

セットアップは、次の順番で行います。

1. GitHub リポジトリを準備する
2. Google Cloud プロジェクトを作成する
3. Google Calendar API を有効化する
4. サービスアカウントを作成する
5. サービスアカウントの JSON キーを発行する
6. 同期専用の Google カレンダーを新規作成する
7. 新しいカレンダーをサービスアカウントと共有する
8. Google カレンダーIDを取得する
9. GitHub Secrets を登録する
10. GitHub Actions を実行する

---

# 1. リポジトリを準備する

このリポジトリを Fork するか、自分の GitHub アカウントへ複製してください。

ローカル環境で取得する場合は、次のコマンドを実行します。

```bash
git clone https://github.com/<YOUR_ACCOUNT>/timetree-sync-action.git
cd timetree-sync-action
```

`<YOUR_ACCOUNT>` は自分の GitHub ユーザー名に置き換えてください。

---

# 2. Google Cloud プロジェクトを作成する

Google Cloud Console を開き、新しいプロジェクトを作成します。

https://console.cloud.google.com/

プロジェクト名は自由です。

例：

```text
TimeTree Sync Action
```

---

# 3. Google Calendar API を有効化する

Google Cloud Console で、次の順番に進みます。

```text
API とサービス
↓
ライブラリ
↓
Google Calendar API
↓
有効にする
```

これで、このプログラムから Google Calendar API を利用できるようになります。

---

# 4. サービスアカウントを作成する

Google Cloud Console で、次の順番に進みます。

```text
IAM と管理
↓
サービス アカウント
↓
サービス アカウントを作成
```

サービスアカウント名は自由です。

例：

```text
timetree-sync-action
```

サービスアカウントを作成すると、次のようなメールアドレスが発行されます。

```text
timetree-sync-action@your-project.iam.gserviceaccount.com
```

このメールアドレスは、後ほど Google カレンダーを共有するときに使用します。

---

# 5. サービスアカウントの JSON キーを発行する

作成したサービスアカウントを開きます。

次の順番に進みます。

```text
キー
↓
鍵を追加
↓
新しい鍵を作成
↓
JSON
↓
作成
```

JSON ファイルがダウンロードされます。

このファイルは、後ほど GitHub Secret の

```text
GOOGLE_SERVICE_ACCOUNT_JSON
```

に登録します。

> [!CAUTION]
> JSON キーには認証情報が含まれています。
>
> GitHub リポジトリへ直接コミットしたり、公開したりしないでください。

---

# 6. 同期専用の Google カレンダーを新規作成する

このプロジェクトでは、TimeTree の同期先として**新しい専用カレンダーを作成することをおすすめします。**

Google Calendar をパソコンのブラウザで開きます。

左側の

```text
他のカレンダー
```

の横にある `+` をクリックし、

```text
新しいカレンダーを作成
```

を選択します。

カレンダー名を入力します。

例：

```text
TimeTree Sync
```

または

```text
TimeTree
```

入力後、

```text
カレンダーを作成
```

をクリックします。

これで TimeTree の予定を同期する専用カレンダーが作成されます。

> [!IMPORTANT]
> メインカレンダーではなく、新しく作成した専用カレンダーを同期先として使用することをおすすめします。
>
> TimeTree から同期された予定だけを分離して管理できるため、確認やメンテナンスが簡単になります。

---

# 7. 新しい Google カレンダーをサービスアカウントと共有する

作成したカレンダーを、このプログラムで使用するサービスアカウントと共有します。

Google Calendar で、次の順番に進みます。

```text
マイカレンダー
↓
作成した「TimeTree Sync」カレンダー
↓
︙
↓
設定と共有
```

共有設定の項目から、サービスアカウントのメールアドレスを追加します。

例：

```text
timetree-sync-action@your-project.iam.gserviceaccount.com
```

このプログラムは予定の追加・更新だけを行い、削除は行いません。

少なくとも、予定を変更できる権限が必要です。

権限が不足している場合、次のようなエラーが発生します。

```text
403 You need to have writer access to this calendar.
```

---

# 8. GOOGLE_CALENDAR_ID を取得する

先ほど作成した同期専用カレンダーのIDを取得します。

Google Calendar で、次の順番に進みます。

```text
マイカレンダー
↓
作成した「TimeTree Sync」カレンダー
↓
︙
↓
設定と共有
↓
カレンダーの統合
↓
カレンダー ID
```

表示された値をコピーします。

通常、新しく作成したカレンダーのIDは次のような形式です。

```text
xxxxxxxxxxxxxxxxxxxxxxxx@group.calendar.google.com
```

この値を、GitHub Secret の

```text
GOOGLE_CALENDAR_ID
```

に登録します。

> [!IMPORTANT]
> このREADMEでは、メインカレンダーではなく、新しく作成した専用カレンダーのIDを設定することを推奨します。

---

# 9. GitHub Secrets を登録する

GitHub で、このリポジトリを開きます。

次の順番に進みます。

```text
Settings
↓
Secrets and variables
↓
Actions
↓
New repository secret
```

以下の5つの Secrets を登録してください。

| Secret                        | 説明                                   |
| ----------------------------- | ------------------------------------ |
| `TIMETREE_EMAIL`              | TimeTree にログインするメールアドレス              |
| `TIMETREE_PASSWORD`           | TimeTree のパスワード                      |
| `TIMETREE_CALENDAR_CODE`      | 同期対象となる TimeTree カレンダーの Alias Code   |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Cloud で発行したサービスアカウント JSON の全文 |
| `GOOGLE_CALENDAR_ID`          | 新しく作成した同期専用 Google カレンダーの ID         |

---

## TIMETREE_EMAIL

TimeTree にログインするときに使用しているメールアドレスを登録します。

例：

```text
example@example.com
```

---

## TIMETREE_PASSWORD

TimeTree にログインするときに使用しているパスワードを登録します。

> [!CAUTION]
> パスワードをソースコードや公開リポジトリへ直接記載しないでください。
>
> 必ず GitHub Secrets を使用してください。

---

## TIMETREE_CALENDAR_CODE

同期したい TimeTree カレンダーの Alias Code を登録します。

利用可能なカレンダーがログに表示される場合、次のような形式で確認できます。

```text
Available TimeTree calendars

- 仕事 (code=AbCdEf12)
- プライベート (code=GhIjKl34)
- 家族 (code=MnOpQr56)
```

例えば、次のカレンダーを同期する場合、

```text
- 家族 (code=MnOpQr56)
```

GitHub Secret には次の値を登録します。

```text
MnOpQr56
```

Secret 名：

```text
TIMETREE_CALENDAR_CODE
```

> [!NOTE]
> `TIMETREE_CALENDAR_CODE` は、TimeTree のカレンダーを識別する Alias Code です。

---

## GOOGLE_SERVICE_ACCOUNT_JSON

Google Cloud でダウンロードしたサービスアカウントの JSON ファイルをテキストエディタで開きます。

ファイルの内容全体をコピーし、そのまま GitHub Secret に登録してください。

Secret 名：

```text
GOOGLE_SERVICE_ACCOUNT_JSON
```

登録する内容の例：

```json
{
  "type": "service_account",
  "project_id": "example-project",
  "private_key_id": "...",
  "private_key": "...",
  "client_email": "timetree-sync-action@example-project.iam.gserviceaccount.com"
}
```

実際には、省略せずJSONファイルの内容全体を登録してください。

---

## GOOGLE_CALENDAR_ID

「6. 同期専用の Google カレンダーを新規作成する」で作成したカレンダーのIDを登録します。

例：

```text
xxxxxxxxxxxxxxxxxxxxxxxx@group.calendar.google.com
```

Secret 名：

```text
GOOGLE_CALENDAR_ID
```

---

# 10. GitHub Actions を実行する

すべての Secrets を登録したら、GitHub のリポジトリを開きます。

次の順番に進みます。

```text
Actions
↓
Sync TimeTree to Google Calendar
↓
Run workflow
```

手動実行は `dry_run=true` が初期値です。まずこのまま実行してログの
`Sync plan: create=... update=... skip=... preserve=... delete=0` を確認してください。
このプレビューでは Google Calendar を変更しません。反映するときだけ
`dry_run` のチェックを外して再実行します。定期実行は追加・更新を反映します。

本番反映前に安全性テストが自動実行されます。初回の追加には時間がかかる場合があります。

実行が成功すると、GitHub Actions に緑色のチェックマークが表示されます。

---

# 自動実行

GitHub Actions のワークフローが次のように設定されている場合、このプログラムは6時間ごとに自動実行されます。

```yaml
on:
  workflow_dispatch:

  schedule:
    - cron: "15 */6 * * *"
```

`workflow_dispatch` により、GitHub Actions の画面から手動実行することもできます。

`cron` の時刻は UTC 基準です（日本時間 03:15 / 09:15 / 15:15 / 21:15）。
GitHub 側の混雑で遅延する場合があります。デフォルトブランチ上の設定が有効です。
手動・定期実行は同じ concurrency ロックを使い、重複実行を避けます。
既存の認証情報・Google カレンダーの共有権限は変更しません。

---

# ローカルで実行する場合

## 1. Python仮想環境を作成する

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

## 2. 必要なライブラリをインストールする

```bash
pip install -r requirements.txt
pip install ./vendor/timetree_exporter
```

---

## 3. 環境変数を設定する

次の環境変数が必要です。

```text
TIMETREE_EMAIL
TIMETREE_PASSWORD
TIMETREE_CALENDAR_CODE
GOOGLE_SERVICE_ACCOUNT_JSON
GOOGLE_CALENDAR_ID
```

設定後、次のコマンドで実行します。

```bash
python src/main.py
```

---

# 検証と実行ログ

認証情報なし・外部アクセスなしで実行するテスト:

```bash
python -m unittest discover -s tests -v
ruff check src tests
```

既存の環境変数を使い、読み取りだけで件数を確認:

```bash
python src/main.py --dry-run
```

同期前に計画件数、完了時に実行件数を出します（予定の内容や認証情報は出しません）。

```text
Sync plan: create=2 update=1 skip=10 preserve=0 delete=0
Sync complete: created=2 updated=1 deleted=0
```

`preserve` は所有元不明・重複・キャンセル済み・ETag不明の既存予定などを保持した件数です。
取得失敗・不正なページ・不完全な予定・重複した元IDは、書き込み前に停止します。
空の取得結果なら何も変更しません。取得結果にない Google の予定もそのまま保持します。
実行途中で通信エラーが発生すると、その時点で停止します。既に完了した追加・更新は
巻き戻さず、次回の読み取り・差分照合で続行します。

終日予定は同梱の TimeTree exporter の仕様に合わせ、最終日を含む元データから
Google の「終了日を含まない」形式へ変換します。時刻付き予定は元のタイムゾーンを使用します。

---

# Google Calendar の既存予定について

このプログラムは、TimeTree のイベントIDと所有元マーカーが一致する予定だけを更新します。

Google Calendar で直接作成した通常の予定は、TimeTree のイベントIDを持たないため、同期処理によって削除されません。

そのため、既存の Google カレンダーを同期先として利用することもできます。

ただし、予定を分かりやすく管理するため、**TimeTree 同期専用の新しい Google カレンダーを作成することをおすすめします。**

---

# ディレクトリ構成

```text
src/
├── config.py
├── logger.py
├── main.py
├── sync.py
├── models/
├── timetree/
└── gcalendar/

vendor/
└── timetree_exporter/
```

---

# トラブルシューティング

## 403 You need to have writer access to this calendar.

サービスアカウントに、Google カレンダーの予定を変更する権限がありません。

次の内容を確認してください。

1. `GOOGLE_CALENDAR_ID` に正しいカレンダーIDが登録されているか
2. 対象の Google カレンダーがサービスアカウントと共有されているか
3. サービスアカウントに予定を変更できる権限が付与されているか

---

## TimeTree のカレンダーが見つからない

`TIMETREE_CALENDAR_CODE` が正しいか確認してください。

同期対象となる TimeTree カレンダーの Alias Code を設定する必要があります。

---

## GitHub Actions が自動実行されない

次の内容を確認してください。

1. ワークフローファイルがデフォルトブランチに存在するか
2. GitHub Actions が有効になっているか
3. `schedule` がワークフローファイルに設定されているか

---

# セキュリティ

次の情報は、ソースコードや公開リポジトリへ直接記載しないでください。

* TimeTree のメールアドレス
* TimeTree のパスワード
* Google サービスアカウントの JSON キー
* その他の認証情報

認証情報は GitHub Secrets または環境変数を使用して管理してください。

---

# ライセンス

このプロジェクトは **MIT License** のもとで公開されています。

同梱している `vendor/timetree_exporter` は、元プロジェクトのライセンスに従って同梱されています。

詳細は `LICENSE` および `NOTICE` を参照してください。
