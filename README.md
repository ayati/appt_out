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

---

## ADB バイナリ形式について

HP 200LX の Appointment Book ファイル形式の概要です。

```
[4 bytes]   マジック: "hcD\0"
[25 bytes]  データベースヘッダ (DBHDR)
[可変長]    レコード列
              各レコード: 6バイト RHDR + データ
[終端]      TYPE_VIEWPTTABLE (type=10) + TYPE_LOOKUPTABLE (type=31)
```

**データレコード (TYPE_DATA=11) のデータ種別:**

| cFlags 値 | 種別 |
|-----------|------|
| 128 以上 | Appointment |
| 32〜127 | Event |
| 16〜31 | ToDo |

**繰り返し種別 (cRepeatType):**

| 値 | 種別 |
|----|------|
| 1 | 繰り返しなし |
| 2 | 毎日 |
| 4 | 毎週 |
| 8 | 毎月 |
| 16 | 毎年 |
| 32 | カスタム |

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
| Phone Book (`*.pdb`) | ❌ 未対応 |
| Database (`*.gdb`) | ❌ 未対応 |
| NoteTaker (`*.ndb`) | ❌ 未対応 |
| ヘッダ出力 (`-h`) | ❌ 未実装 |

---

## ライセンス

本ツールは APPTOUT 0.86b (Copyright (C) 1994-2000 by nuisance) のバイナリフォーマット解析をもとに Python 3 で独自実装したものです。

---

## 謝辞

HP 200LX 向け PIM ファイル形式の解析において、APPTOUT 0.86b のソースコード (nuisance 氏作) を参考にしました。
