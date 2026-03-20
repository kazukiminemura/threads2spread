# threads2spread

Threads の検索結果を集めて JSON に保存し、その JSON をもとに Threads 投稿案を生成するための小さなツール集です。

現在の主なスクリプトは次の 4 つです。

- `search_threads_top_keyword.py`
  Threads をキーワード検索し、上位投稿を `outputs/search_results/` に JSON 保存します。
- `generate_threads_content.py`
  検索結果 JSON を OpenClaw の ACP runtime backend に渡し、投稿案を `outputs/generated_posts/` に JSON 保存します。
- `export_threads_csv.py`
  生成済みの投稿案 JSON を、予約投稿用の CSV に変換します。
- `append_csv_to_google_sheet.py`
  生成済みの CSV を Google スプレッドシートに追記します。

## ワークフロー概要

このツールは、次の流れで `キーワード検索 → コンテンツ生成 → CSV化 → 予約投稿` を進めます。

1. `search_threads_top_keyword.py`
   指定したキーワードで Threads を検索し、上位投稿を `outputs/search_results/` に JSON 保存します。
2. `generate_threads_content.py`
   検索結果 JSON をもとに、Threads 投稿案を `outputs/generated_posts/` に JSON 保存します。
3. `export_threads_csv.py`
   生成した投稿案 JSON を、予約投稿に使える CSV に変換して `outputs/post_csv/` に保存します。
4. `append_csv_to_google_sheet.py`
   作成した CSV を Google スプレッドシートに追記し、予約投稿用の管理表として使える状態にします。

### 最短の使い方

たとえば `金運` というキーワードで一連の流れを実行する場合は次のとおりです。

```bash
./venv/bin/python search_threads_top_keyword.py "金運"
./venv/bin/python generate_threads_content.py
./venv/bin/python export_threads_csv.py --scheduled-date 2126/03/19 --scheduled-time 23:45
./venv/bin/python append_csv_to_google_sheet.py
```

各ステップの出力は次のステップの入力になります。

- 検索結果 JSON
  `outputs/search_results/` に保存され、コンテンツ生成で使われます。
- 投稿案 JSON
  `outputs/generated_posts/` に保存され、CSV 化で使われます。
- 予約投稿 CSV
  `outputs/post_csv/` に保存され、Google スプレッドシートへの追記に使われます。

## Requirements

- Python 3.10+
- Playwright / Chromium
- OpenClaw

`requirements.txt`:

```bash
pip install -r requirements.txt
```

## Setup

### 1. Python 環境

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 2. Playwright / Chromium

```bash
./venv/bin/python -m playwright install
./venv/bin/python -m playwright install-deps
```

`search_threads_top_keyword.py` と `append_csv_to_google_sheet.py` は初回起動時に上の 2 コマンドを自動実行します。完了状態は `.playwright-browser-installed` と `.playwright-deps-installed` で個別に管理し、旧 `.playwright-installed` しかない環境でも不足分を自動補完します。

### 3. OpenClaw

このリポジトリでは `generate_threads_content.py` から `openclaw` コマンドを呼び出します。  
PATH 上にない場合でも、次のユーザー領域パスは自動で探索します。

- `~/.npm-global/bin/openclaw`
- `~/.local/bin/openclaw`

### 4. LLM Provider 設定

`generate_threads_content.py` は、OpenClaw 側であらかじめ設定された LLM を使います。  
このスクリプト自体は Ollama 前提ではなく、利用するモデルや API キーの設定は OpenClaw 側で管理する想定です。

## 1. Threads を検索して JSON 保存

キーワードを検索して、上位 10 件までの結果を JSON 保存します。

```bash
./venv/bin/python search_threads_top_keyword.py "金運"
```

JSON も標準出力したい場合:

```bash
./venv/bin/python search_threads_top_keyword.py "金運" --json
```

件数を変えたい場合:

```bash
./venv/bin/python search_threads_top_keyword.py "金運" --limit 5
```

出力先:

- `outputs/search_results/<timestamp>_<keyword>.json`

保存される JSON には次のような情報が含まれます。

- `keyword`
- `results_count`
- `results[].title`
- `results[].content`
- `results[].link`

## 2. 検索結果 JSON から投稿案を生成

最新の検索結果 JSON を読み込み、OpenClaw の ACP runtime backend 経由で投稿案を生成します。

```bash
./venv/bin/python generate_threads_content.py
```

投稿数を変える場合:

```bash
./venv/bin/python generate_threads_content.py --count 5
```

長さプリセットを変える場合:

```bash
./venv/bin/python generate_threads_content.py --content-length short
./venv/bin/python generate_threads_content.py --content-length medium
./venv/bin/python generate_threads_content.py --content-length long
```

最大文字数で制御する場合:

```bash
./venv/bin/python generate_threads_content.py --max-chars 120
```

特定の検索結果 JSON を使う場合:

```bash
./venv/bin/python generate_threads_content.py \
  --results-file outputs/search_results/20260320_114150_金運.json
```

出力先:

- `outputs/generated_posts/<timestamp>_<keyword>_threads_posts.json`

出力 JSON には次のような情報が入ります。

- `generator`
- `backend`
- `model`
- `keyword`
- `posts`
- `raw_response`

## 3. 生成済み JSON から投稿予約用 CSV を作成

最新の `outputs/generated_posts/*.json` を読み込み、次の列順で CSV を作成します。

- `ID`
- `投稿内容`
- `予定日付`
- `予定時刻`
- `ステータス`
- `投稿URL`
- `ツリーID`
- `投稿順序`
- `動画URL`
- `画像URL_1枚目` から `画像URL_10枚目`

基本実行:

```bash
./venv/bin/python export_threads_csv.py
```

特定の JSON を指定する場合:

```bash
./venv/bin/python export_threads_csv.py \
  --input-file outputs/generated_posts/20260320_180000_intel_threads_posts.json
```

予定日付と予定時刻を一括指定する場合:

```bash
./venv/bin/python export_threads_csv.py \
  --scheduled-date 2126/03/19 \
  --scheduled-time 23:45
```

出力先:

- `outputs/post_csv/<input_filename>.csv`

現在の `generate_threads_content.py` の出力には画像 URL やツリー情報がないため、それらの列は空欄になります。  
将来 JSON 側に `thread_id`, `post_order`, `video_url`, `image_urls` などを入れれば、そのまま CSV に反映されます。

## 4. CSV を Google スプレッドシートに追記

最新の `outputs/post_csv/*.csv` を、`.env` または CLI で指定した Google スプレッドシートのタブ末尾に追記します。

基本実行:

```bash
./venv/bin/python append_csv_to_google_sheet.py
```

デフォルトでは Playwright で Google Sheets を直接開いて追記します。  
初回はブラウザが開くので、必要ならその場で Google にログインしてください。ログイン状態は `.playwright-google-sheets-profile/` に保存されます。

特定の CSV を指定する場合:

```bash
./venv/bin/python append_csv_to_google_sheet.py \
  --csv-file outputs/post_csv/20260329_081024_nvidia_threads_posts.csv
```

service account を使って Google Sheets API 経由で追記したい場合:

```bash
./venv/bin/python append_csv_to_google_sheet.py \
  --mode api \
  --service-account-file /absolute/path/to/service-account.json
```

Google Cloud の設定手順:

1. Google Cloud Console を開く
2. 右上でプロジェクトを選ぶ。なければ新規作成
3. `APIs とサービス` → `ライブラリ`
4. `Google Sheets API` を検索して `有効にする`
5. `IAM と管理` → `サービス アカウント`
6. `サービス アカウントを作成`
7. 名前だけ入れて作成
8. 作成したサービスアカウントを開く
9. `キー` タブ → `鍵を追加` → `新しい鍵を作成`
10. `JSON` を選んで作成
11. ダウンロードされた JSON を安全な場所に置く
    例: `/home/threads-001/keys/google-sheets-sa.json`

ヘッダー行も含めて追記する場合:

```bash
./venv/bin/python append_csv_to_google_sheet.py --include-header
```

`.env` の例:

```bash
GOOGLE_SHEETS_URL=https://docs.google.com/spreadsheets/d/your-spreadsheet-id/edit?gid=your-gid#gid=your-gid
GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/service-account.json
```

CLI で URL を渡す場合:

```bash
./venv/bin/python append_csv_to_google_sheet.py \
  --spreadsheet-url "https://docs.google.com/spreadsheets/d/your-spreadsheet-id/edit?gid=your-gid#gid=your-gid"
```

デフォルトではヘッダー行を除いたデータ行だけを、指定した URL の `gid` に対応するシート末尾へ追記します。  
`--mode auto` のときは service account が見つかれば API、なければブラウザ操作を使います。

## 実行フロー

### 検索フロー

1. Threads の検索画面を開く
2. キーワードを入力する
3. 上位投稿リンクを収集する
4. 各投稿ページを開いて本文を抽出する
5. `outputs/search_results/` に JSON 保存する

### 投稿案生成フロー

1. 最新または指定された検索結果 JSON を読む
2. OpenClaw の設定済みモデルで実行する
3. 一時 Gateway を起動する
4. ACP bridge を立ち上げる
5. ACP runtime backend に prompt を送る
6. 返ってきた内容から投稿案 JSON を保存する

### CSV 変換フロー

1. 最新または指定された投稿案 JSON を読む
2. 投稿本文を予約投稿 CSV の列に割り当てる
3. `outputs/post_csv/` に CSV 保存する

### Google Sheets 追記フロー

1. 最新または指定された CSV を読む
2. スプレッドシートURLから spreadsheet id と gid を取り出す
3. `auto` なら API またはブラウザ操作を選ぶ
4. ブラウザ操作または API で対象シートを開く
5. CSV の内容をシート末尾へ追記する

## 注意点

- Threads 検索はブラウザ表示やログイン状態に依存します。
- 投稿本文の抽出は Threads 側の DOM 変更で影響を受ける可能性があります。
- `generate_threads_content.py` は OpenClaw / ACP と、その先で設定された LLM provider の状態に依存します。
- ブラウザ操作で使う場合は、Google Sheets にアクセスできる Google アカウントでログインしている必要があります。
- API で使う場合は、対象シートが service account に共有されている必要があります。
- `outputs/` や Playwright の profile ディレクトリは通常コミットしません。

## Files

- [Readme.md](/home/threads-001/projects/threads2spread/Readme.md)
- [search_threads_top_keyword.py](/home/threads-001/projects/threads2spread/search_threads_top_keyword.py)
- [generate_threads_content.py](/home/threads-001/projects/threads2spread/generate_threads_content.py)
- [export_threads_csv.py](/home/threads-001/projects/threads2spread/export_threads_csv.py)
- [append_csv_to_google_sheet.py](/home/threads-001/projects/threads2spread/append_csv_to_google_sheet.py)
- [requirements.txt](/home/threads-001/projects/threads2spread/requirements.txt)
