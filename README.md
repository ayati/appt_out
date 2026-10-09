# appt_out.py

HP 200LX の Appointment Book ファイル (`*.adb`) を読み書きする Python 3 製 CLI ツール。

MS-DOS 時代のユーティリティ **APPTOUT 0.86b** (by nuisance) の機能を Python 3 で再実装し、
さらに Google Calendar の ICS ファイルからのインポートに対応しています。

---

## 機能

| 機能 | 内容 |
|------|------|
| ADB → CSV/TSV 出力 | Appointment / Event / ToDo を CSV またはタブ区切りでエクスポート |
| CSV → ADB 取り込み | CSV ファイルの内容を ADB ファイルへ追加・新規作成 |
| ICS → ADB 取り込み | Google Calendar の `.ics` ファイルを ADB へインポート (種別自動判定) |
| ADB 複製・マージ | 既存 ADB をベースに新しい ADB を生成 |
| 日付範囲フィルタ | 開始日・終了日を指定して出力を絞り込み |
| 文字エンコーディング | ADB: Shift-JIS (CP932) / CSV: UTF-8 または Shift-JIS 選択可 |

## 対応ファイル種別

- `*.adb` — Appointment Book (読み書き)
- `*.csv` — CSV ファイル (読み書き、UTF-8 / Shift-JIS)
- `*.ics` — iCalendar / Google Calendar エクスポート (読み込みのみ、UTF-8)

> `*.pdb` (Phone Book) / `*.gdb` (Database) / `*.ndb` (NoteTaker) は現時点で非対応です。

---

## 動作要件

- Python 3.8 以上
- 標準ライブラリのみ使用 (追加インストール不要)

---

## インストール

```bash
git clone <リポジトリURL>
cd appt_out
python3 appt_out.py --help
```

---

## 使い方

### 基本構文

```
python3 appt_out.py -x <入力ADB> [オプション] [--ics <ICS>] [-i <CSV>] [-o <出力ADB>]
```

### オプション一覧

| オプション | 説明 |
|-----------|------|
| `-x FILE` | 入力 ADB ファイル **(必須)** |
| `--ics FILE` | 取り込む ICS ファイル (種別自動判定・dedup ON・前年以降フィルタがデフォルト) |
| `--ics-from YYMMDD` | ICS 取り込み開始日 (デフォルト: 前年1/1。`0` でフィルタなし) |
| `-i FILE` | 取り込む CSV ファイル (要 `-a`/`-e`/`-t`) |
| `-o FILE` | 出力 ADB ファイル |
| `-a [N]` | Appointment を対象にする (N: 0=無効 1=全て 2=非繰り返しのみ 3=繰り返しのみ) |
| `-e [N]` | Event を対象にする (N: 同上) |
| `-t [N]` | ToDo を対象にする (N: 0=無効 1=全て 2=非繰り返し 3=繰り返し 4=Appt形式で出力) |
| `-m` | 新規作成モード (既存データをコピーせず取り込み内容だけで新規作成) |
| `--dedup` | CSV 取り込み時の重複スキップを有効化 |
| `--no-dedup` | ICS 取り込み時の重複スキップを無効化 (ICS デフォルトは ON) |
| `-n [N]` | NOTE の改行処理 (0=そのまま 1=スペースに変換 2=`\n` 文字列に変換, デフォルト: 2) |
| `-r [N]` | アラーム・月表示・週表示設定 0〜7 (デフォルト: 7 = 全ON) |
| `-g YYMMDD` | CSV 出力開始日 (例: `260101` → 2026年1月1日) |
| `-f YYMMDD` | CSV 出力終了日 (例: `261231` → 2026年12月31日) |
| `-b` | タブ区切り出力 (デフォルトは CSV) |
| `--csv-encoding` | CSV 入出力エンコーディング (`utf-8` / `cp932` / `shift-jis`, デフォルト: `utf-8`) |
| `--ics-encoding` | ICS 入力エンコーディング (デフォルト: 自動検出。例: `utf-8`, `cp932`) |
| `-s [N]` | 進捗出力の抑制 (0=通常 1=進捗非表示 2=全て抑制) |

> `-a` / `-e` / `-t` は同時に指定できません。  
> `--ics` と `-i` は同時に指定できません。  
> `--dedup` と `--no-dedup` は同時に指定できません。

---

## 使用例

### ADB の内容を CSV/TSV で確認する

```bash
# Appointment を標準出力へ (CSV)
python3 appt_out.py -x APPT.adb -a

# Event をタブ区切りで出力
python3 appt_out.py -x APPT.adb -e -b > events.tsv

# ToDo を Shift-JIS CSV で出力 (元の apptout との互換)
python3 appt_out.py -x APPT.adb -t --csv-encoding cp932 > todo_sjis.csv

# 2026年の Appointment だけ出力
python3 appt_out.py -x APPT.adb -a -g 260101 -f 261231
```

### ADB を別ファイルへコピーする

```bash
python3 appt_out.py -x APPT.adb -o APPT_backup.adb
```

### ICS → ADB インポート (Google Calendar の予定を追加)

```bash
# 基本: 終日イベント→Event, 時間付き→Appointment で自動判定。前年1/1以降のみ取り込み。重複スキップON。
python3 appt_out.py -x APPT.adb --ics basic.ics -o new.adb

# フィルタなし (全期間取り込み)
python3 appt_out.py -x APPT.adb --ics basic.ics -o new.adb --ics-from 0

# 2025年以降のみ取り込み (明示指定)
python3 appt_out.py -x APPT.adb --ics basic.ics -o new.adb --ics-from 250101

# 重複スキップを無効化して全件追加
python3 appt_out.py -x APPT.adb --ics basic.ics -o new.adb --no-dedup

# 既存データを引き継がず ICS の内容だけで新規作成
python3 appt_out.py -x APPT.adb --ics basic.ics -o new.adb -m
```

### CSV → ADB インポート

```bash
# UTF-8 CSV を Appointment として追加
python3 appt_out.py -x APPT.adb -i input.csv -o new.adb -a

# 旧 apptout 形式の Shift-JIS CSV を取り込む
python3 appt_out.py -x APPT.adb -i old_sjis.csv -o new.adb -e --csv-encoding cp932

# 重複スキップ有効
python3 appt_out.py -x APPT.adb -i input.csv -o new.adb -a --dedup
```

---

## ICS 取り込み動作仕様

| 項目 | デフォルト動作 | 変更方法 |
|------|--------------|---------|
| 種別判定 | DTSTART に時刻あり→Appointment、終日→Event | (変更不可、自動) |
| 日付フィルタ | 前年 1/1 より前は除外 | `--ics-from YYMMDD` または `--ics-from 0` |
| 重複スキップ | ON (既存ADBと件名・日付・時刻が一致する予定はスキップ) | `--no-dedup` で無効化 |

### ICS の各プロパティの扱い

| ICS | ADB での扱い |
|-----|-------------|
| `SUMMARY` | Description (51 バイトを超える分は切り詰め) |
| `LOCATION` | Location |
| `DESCRIPTION` | NOTE (`\n` `\,` `\;` のエスケープを展開) |
| `DTSTART` (UTC `Z` 付き) | JST (UTC+9) に変換 |
| `DTEND` なしの時刻付き予定 | 終了時刻 = 開始時刻 (RFC 5545 の期間 0) |
| 複数日の終日イベント | 開始日のみの 1 日イベント (連続日数 0) |
| `RRULE` | 繰り返し予定 (下記) |
| `EXDATE` | 繰り返しの削除済み発生日リストに登録 |
| `RECURRENCE-ID` (変更された回) | 元の発生日を親の削除済みリストに入れ、変更後の回を単独の予定として登録。初回を同じ日のまま変更した回は親に内容を反映して 1 件にまとめる |

### RRULE の変換

| RRULE | ADB の繰り返し |
|-------|---------------|
| `FREQ=DAILY` | 毎日 (`INTERVAL` → 間隔) |
| `FREQ=WEEKLY;BYDAY=MO,WE` | 毎週 (曜日ビット)。`BYDAY` 省略時は DTSTART の曜日 |
| `FREQ=MONTHLY` | 毎月 `BYMONTHDAY` 日。省略時は DTSTART の日 |
| `FREQ=MONTHLY;BYDAY=3TH` | 毎月 第3木曜 (`-1FR` = 最終金曜) |
| `FREQ=YEARLY` | 毎年。`BYMONTH` 省略時は DTSTART の月、日は DTSTART の日 |
| `FREQ=YEARLY;BYMONTH=10;BYDAY=2SA` | 毎年 10月第2土曜 |
| `UNTIL=...` | 繰り返し終了日 |
| `UNTIL` なし (無期限) | 終了日 2099/12/31 |

- 第5週 (`5FR` など) は HP 200LX で表現できないため、DTSTART の日付による繰り返しとして登録し、警告を表示します。
- `COUNT` (回数指定) は未対応です (無期限として扱います)。

---

## CSV フォーマット

### Appointment / Event

```
"Description","YYYY/MM/DD","HH:MM"|"NONE","HH:MM"|"NONE","Location","ConsecDays","Note"
```

繰り返し設定がある場合、末尾に以下のフィールドが追加されます。

```
"Freq","Days","Months","RepeatStart","RepeatEnd","DeleteCount"
```

| フィールド | 内容 |
|-----------|------|
| Description | 件名 (最大 51 バイト / Shift-JIS) |
| YYYY/MM/DD | 開始日 (空欄 = 日付なし) |
| HH:MM / NONE | 開始時刻 / 終了時刻 |
| Location | 場所 (最大 19 バイト / Shift-JIS) |
| ConsecDays | 連続日数 (0 = 1日のみ) |
| Note | メモ (改行は `\n` で表現, `-n` オプションで変更可) |

### ToDo

```
"Description","YYYY/MM/DD","ConsecDays","CompleteDate","Priority","Note"
```

| フィールド | 内容 |
|-----------|------|
| ConsecDays | 期限日数 |
| CompleteDate | 完了日 (空欄 = 未完了) |
| Priority | 優先度 (1 文字) |

### 繰り返しフィールド (Days / Months) の注意

- `Months` の `NOV` / `DEC` は実機に合わせて NOV=0x400, DEC=0x800 で読み書きします (旧 APPTOUT は逆)。
- `Days` の第N週表記 (`2th,SAT` など) の数値は、旧 APPTOUT 互換で週フィールドの値をそのまま書きます。実機の週はビット (下記) のため、第1・第2週は `1th` `2th` ですが、第3週は `4th`、第4週は `8th`、最終週は `16th` になります。ADB → CSV → ADB の往復では値は保たれます。

---

## ADB バイナリ形式について

HP 200LX の Appointment Book ファイル形式の概要です。
APPTOUT のソースに加え、実機 (HP 200LX) で手入力したデータとの比較で確認した内容を含みます。

```
[4 bytes]   マジック: "hcD\0"
[25 bytes]  データベースヘッダ (DBHDR)  … iNumRecords (offset 12), LUT 位置 (offset 14)
[可変長]    レコード列
              各レコード: 6バイト RHDR (cType, cStatus, iLength, iRecord) + データ
              FieldDef(6) / LinkDef(12) / CardPageDef(15)
              → DATA(11) と NOTE(9) → TYPE_14[0] / TYPE_14[1]
[終端]      TYPE_VIEWPTTABLE (type=10) + TYPE_LOOKUPTABLE (type=31)
```

### レコードの引き方 (LookupTable)

- LookupTable (LUT) は全レコードの位置を **cType 順** に並べた 8 バイトエントリの表と、
  その後ろの「cType ごとの開始インデックス」(32 個の short) からなります。
- 実機はレコードを **「cType の開始インデックス + iRecord」** で引きます。
  そのため iRecord は **種別ごとに 0 から連番** でなければなりません。
  DATA と NOTE の iRecord が重複するのは正常です。
- NOTE の iRecord を DATA 件数の続きから振ると、DATA の `iNoteRecNum` が NOTE の範囲外を指し、
  一覧では表題が見えるのに、開くと `Record not found` になります。

### データレコード (TYPE_DATA=11)

27 バイトの固定部 (NRA) の後に Description / Category / Location (NULL 終端, Shift-JIS) と、
繰り返しがあれば繰り返し情報 (RIA) が続きます。

| offset | 内容 |
|-------:|------|
| 0 | iLength |
| 2 / 4 / 6 | Category / Location / 繰り返し情報 のオフセット |
| 8 | iNoteRecNum (NOTE の iRecord, -1 = なし) |
| 10 / 12 | iPrevRecNum / iNextRecNum (連結リスト, 下記) |
| 14 | cFlags (種別 + アラーム/月表示/週表示) |
| 15–17 | 開始日 (年-1900, 月-1, 日-1) |
| 18–19 | 開始時刻 (分, LE)。時刻なし = `FF FF` |
| 20–21 | 連続日数 (iEndDate) |
| 22–23 | 終了時刻 (分, LE)。時刻なし = `FF FF` |
| 24 / 25 | アラームのリードタイム |
| 26 | cRepeatType |

**cFlags のデータ種別:**

| cFlags 値 | 種別 |
|-----------|------|
| 128 以上 | Appointment |
| 32〜127 | Event |
| 16〜31 | ToDo |

> 時刻なしは実機では `FF FF` です。旧 APPTOUT は `00 00` を書くため 0:00 扱いになり、
> 繰り返しイベントを実機で編集するとエラーデータになりました。
> 本ツールは新規レコードを `FF FF` で書き、既存レコードは読み込んだ値のまま書き戻します。

**繰り返し種別 (cRepeatType):**

| 値 | 種別 |
|----|------|
| 1 | 繰り返しなし |
| 2 | 毎日 |
| 4 | 毎週 |
| 8 | 毎月 |
| 16 | 毎年 |
| 32 | カスタム |

### 繰り返し情報 (RIA)

| offset | 内容 |
|-------:|------|
| 0 | 間隔 (Freq) |
| 1 | Days: 曜日指定時は `0x80` + 曜日ビット、日付指定時は日 (1〜31) |
| 2 | Weeks: 第N週ビット |
| 3–4 | 月ビット (LE) |
| 5–7 | 繰り返し開始日 (年-1900, 月-1, 日-1) |
| 8–10 | 繰り返し終了日 (同上) |
| 11 | 削除済み発生日の件数 |
| 12〜 | 削除済み発生日 × 件数 (各 4 バイト: 年-1900, 月-1, 日-1, `00`)、日付順 |

| 曜日ビット | 月 | 火 | 水 | 木 | 金 | 土 | 日 |
|---|---|---|---|---|---|---|---|
| | `0x01` | `0x02` | `0x04` | `0x08` | `0x10` | `0x20` | `0x40` |

| 週ビット | 第1週 | 第2週 | 第3週 | 第4週 | 最終週 |
|---|---|---|---|---|---|
| | `0x01` | `0x02` | `0x04` | `0x08` | `0x10` |

月ビットは 1月 = `0x001` 〜 12月 = `0x800` (`1 << (月-1)`)。

実機の手入力データでの例:

| 予定 | Days | Weeks | 月 |
|------|------|-------|----|
| 毎週水曜 | `0x84` | `0x00` | `0x000` |
| 毎月第1木曜 | `0x88` | `0x01` | `0x000` |
| 毎月第3木曜 | `0x88` | `0x04` | `0x000` |
| 毎月最終金曜 | `0x90` | `0x10` | `0x000` |
| 毎年 12/1 | `0x01` | `0x00` | `0x800` |
| 毎年 10月第2土曜 | `0xA0` | `0x02` | `0x200` |

> 年繰り返しは Days と月ビットの両方が必要です。どちらかが 0 だと発生日がなく、
> 初回以降の年に表示されません。

### 連結リスト (iPrevRecNum / iNextRecNum)

- **繰り返しなし**の予定は、同じ日付のレコード同士で連結リストを作ります
  (全日イベントを先頭に、時刻順)。
- **繰り返しあり**の Appointment / Event は日付ごとのリストには入れず、
  **繰り返しレコードだけで 1 本の連結リスト** を作ります。その先頭は TYPE_14[0] に書きます。
  実機はこのリストをたどって繰り返しを展開するため、ここに無いと基準日にしか表示されません。

### TYPE_14 (日付インデックス + 設定)

| レコード | 内容 |
|----------|------|
| TYPE_14[0] bytes 0–2 | 日付 (年-1900, 月-1, 日-1)。実機が更新する (最終使用日と推定) |
| TYPE_14[0] bytes 17–18 | 繰り返しリストの先頭レコード番号 (LE, -1 = なし) |
| TYPE_14[0] bytes 19–20 | 日付インデックスのエントリ数 (LE) |
| TYPE_14[1] | 日付インデックス: 繰り返しなしの各日付リスト先頭について 5 バイト (年, 月, 日, iRecord LE) を日付順に並べ、終端 `5A 00 00 FF FF` |

繰り返しレコードは日付インデックスには入れません。

---

## 元プログラム APPTOUT との互換性

本ツールは APPTOUT 0.86b の以下の機能を再実装しています。

| 機能 | 対応 |
|------|------|
| ADB → CSV 出力 (`-c2` デフォルト形式) | ✅ |
| CSV → ADB 追加登録 | ✅ |
| CSV → ADB 新規作成 (`-m`) | ✅ |
| Shift-JIS CSV 入出力 | ✅ |
| 日付範囲フィルタ (`-g` / `-f`) | ✅ |
| NOTE 改行処理 (`-n`) | ✅ |
| アラーム設定 (`-r`) | ✅ |
| 繰り返しイベント (読み書き) | ✅ |
| タブ区切り出力 (`-b`) | ✅ |
| ICS インポート (拡張機能) | ✅ |
| 繰り返し予定の新規登録 (ICS から) | ✅ (APPTOUT は非繰り返しのみ登録可) |
| Phone Book (`*.pdb`) | ❌ 未対応 |
| Database (`*.gdb`) | ❌ 未対応 |
| NoteTaker (`*.ndb`) | ❌ 未対応 |
| ヘッダ出力 (`-h`) | ❌ 未実装 |

### APPTOUT との動作の違い

実機データとの比較で判明した APPTOUT の不具合は、本ツールでは実機に合わせています。

| 項目 | APPTOUT 0.86b | 本ツール |
|------|---------------|----------|
| 時刻 `NONE` の書き込み | `00 00` (判定式の誤りで 0:00) | `FF FF` |
| 月ビット 11月 / 12月 | 12月=0x400, 11月=0x800 (逆) | 11月=0x400, 12月=0x800 |
| 繰り返し終了日の既定 | 1999 年 | 2099/12/31 (ICS で `UNTIL` なしの場合) |

---

## ライセンス

本ツールは APPTOUT 0.86b (Copyright (C) 1994-2000 by nuisance) のバイナリフォーマット解析をもとに Python 3 で独自実装したものです。

---

## 謝辞

HP 200LX 向け PIM ファイル形式の解析において、APPTOUT 0.86b のソースコード (nuisance 氏作) を参考にしました。
