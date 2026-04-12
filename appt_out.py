#!/usr/bin/env python3
"""
appt_out.py - HP 200LX *.adb (Appointment Book) 入出力ツール
Python3 port of apptout 0.86b by nuisance

Usage:
  python appt_out.py -x APPT.adb                        # ADB→CSV (stdout)
  python appt_out.py -x APPT.adb -o OUT.adb             # ADB複製
  python appt_out.py -x APPT.adb -i IN.csv -o OUT.adb -e  # CSV→ADB追加
  python appt_out.py -x APPT.adb -i IN.ics -o OUT.adb -e  # ICS→ADB追加
"""

import sys
import io
import os
import csv
import struct
import argparse
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict
from datetime import date, datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# ADB バイナリ定数
# ---------------------------------------------------------------------------
ADB_MAGIC = b"hcD\x00"

TYPE_DBHEADER    = 0
TYPE_CARDDEF     = 4
TYPE_CATEGORY    = 5
TYPE_FIELDDEF    = 6
TYPE_VIEWPTDEF   = 7
TYPE_NOTE        = 9
TYPE_VIEWPTTABLE = 10
TYPE_DATA        = 11
TYPE_LINKDEF     = 12
TYPE_CARDPAGEDEF = 13
TYPE_USERTYPE14  = 14   # apptout.exe の "UserType14" (日付インデックス + 設定)
TYPE_LOOKUPTABLE = 31

# cFlags: データ種別
KIND_APPOINTMENT = 128   # cFlags >= 128
KIND_EVENT       = 32    # 32 <= cFlags < 128
KIND_TODO        = 16    # 16 <= cFlags < 32

# cRepeatType
REPEAT_NONE    = 1
REPEAT_DAILY   = 2
REPEAT_WEEKLY  = 4
REPEAT_MONTHLY = 8
REPEAT_YEARLY  = 16
REPEAT_CUSTOM  = 32

# NRA構造体サイズ (frw.cより: 2+2+2+2+2+2+2+1+1+1+1+1+1+2+1+1+1+1+1 = 27)
NRA_SIZE  = 27
RHDR_SIZE = 6
DBHDR_SIZE = 25

# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class RepeatInfo:
    freq: int = 0           # 繰り返し間隔
    days: int = 0           # 曜日ビット (低8bit) + Weeks (高8bit)
    month: int = 0          # 月ビット (Month1 + Month2*256)
    start_year: int = 94    # cStartYear (year-1900)
    start_month: int = 0    # cStartMonth (month-1)
    start_day: int = 0      # cStartDay (day-1)
    end_year: int = 99      # cEndYear
    end_month: int = 0      # cEndMonth
    end_day: int = 0        # cEndDay
    delete_count: int = 0   # cDelete
    deleted: bytes = b""    # 削除済み発生日リスト (4バイト×delete_count)

    @property
    def day_bits(self) -> int:
        return self.days & 0xFF

    @property
    def week_bits(self) -> int:
        return (self.days >> 8) & 0xFF


@dataclass
class Appointment:
    """1件のアポイントメント/イベント/ToDoデータ"""
    # 種別
    kind: int = KIND_APPOINTMENT   # cKind: 128=Appt, 32=Event, 16=ToDo
    flags: int = 135               # cFlags (アラーム/週/月表示フラグ込み)

    # 内容
    description: str = ""
    category: str = ""
    location: str = ""
    note: str = ""

    # 日付 (None = 日付なし)
    start_year: int = 0xFF   # 0xFF=日付なし (raw: year-1900)
    start_month: int = 0xFF  # raw: month-1
    start_day: int = 0xFF    # raw: day-1

    # 時刻 (None = NONE)
    start_time: Optional[int] = None  # 分 (0-1439), None=NONE
    end_time: Optional[int] = None    # 分 (0-1439), None=NONE

    # 連続日数 / ToDo関連
    consec_days: int = 0  # iEndDate

    # ToDo専用フィールド
    complete_year: int = 0xFF   # cEndTimeYear (raw)
    complete_month: int = 0xFF  # cEndTimeMonth (raw)
    complete_day: int = 0xFF    # cLeadTimeDay (raw)
    priority: str = "1"         # cStartTimePri1/2 (ToDoのみ)
    lead_time_day: int = 5      # cLeadTimeDay (Appt/Eventのアラームリードタイム日)
    lead_time: int = 0          # cLeadTime

    # 繰り返し
    repeat_type: int = REPEAT_NONE
    repeat: Optional[RepeatInfo] = None

    # 内部管理
    note_rec_num: int = -1   # -1 = ノートなし
    record_num: int = 0

    @property
    def has_date(self) -> bool:
        return self.start_year != 0xFF

    @property
    def start_date_str(self) -> str:
        if not self.has_date:
            return ""
        return f"{self.start_year+1900:04d}/{self.start_month+1:02d}/{self.start_day+1:02d}"

    @property
    def start_date_int(self) -> int:
        """yyyymmdd形式の整数 (日付フィルタ用)"""
        if not self.has_date:
            return 0
        return (self.start_year+1900)*10000 + (self.start_month+1)*100 + (self.start_day+1)


# ---------------------------------------------------------------------------
# ADB バイナリ読み込み
# ---------------------------------------------------------------------------

def _read_cstring(data: bytes, offset: int) -> str:
    """offsetからNULL終端文字列を返す (Shift-JIS)"""
    end = data.index(b'\x00', offset)
    return data[offset:end].decode('cp932', errors='replace')


def _decode_time(pri1: int, pri2: int) -> Optional[int]:
    """cStartTimePri1/Pri2 → 分 (NONE時はNone)"""
    if pri1 == 0 or pri2 == 0x80:
        return None
    minutes = pri2 * 256 + pri1
    if minutes < 0 or minutes >= 1440:
        return None
    return minutes


def _decode_todo_time(pri1: int, pri2: int) -> Optional[int]:
    """ToDoの終了時刻フィールド (cEndTimeYear/Month) → 分"""
    if pri1 == 0 or pri2 == 0x80:
        return None
    return pri2 * 256 + pri1


def _parse_nra(data: bytes) -> dict:
    """NRA構造体 (27バイト) をdictにデコード"""
    # struct layout (little-endian):
    # iLength(2), iOsCategory(2), iOsLocation(2), iOsRepeat(2),
    # iNoteRecNum(2), iPrevRecNum(2), iNextRecNum(2),
    # cFlags(1), cYear(1), cMonth(1), cDay(1),
    # cStartTimePri1(1), cStartTimePri2(1),
    # iEndDate(2),
    # cEndTimeYear(1), cEndTimeMonth(1),
    # cLeadTimeDay(1), cLeadTime(1), cRepeatType(1)
    fmt = '<7h3B2B2H2B2Bh'  # これは合わない、手動でパース
    # 手動パース
    iLength      = struct.unpack_from('<H', data, 0)[0]
    iOsCategory  = struct.unpack_from('<H', data, 2)[0]
    iOsLocation  = struct.unpack_from('<H', data, 4)[0]
    iOsRepeat    = struct.unpack_from('<H', data, 6)[0]
    iNoteRecNum  = struct.unpack_from('<h', data, 8)[0]   # signed
    iPrevRecNum  = struct.unpack_from('<h', data, 10)[0]
    iNextRecNum  = struct.unpack_from('<h', data, 12)[0]
    cFlags       = data[14]
    cYear        = data[15]
    cMonth       = data[16]
    cDay         = data[17]
    cStartTimePri1 = data[18]
    cStartTimePri2 = data[19]
    iEndDate     = struct.unpack_from('<H', data, 20)[0]
    cEndTimeYear   = data[22]
    cEndTimeMonth  = data[23]
    cLeadTimeDay   = data[24]
    cLeadTime      = data[25]
    cRepeatType    = data[26]
    return dict(
        iLength=iLength, iOsCategory=iOsCategory,
        iOsLocation=iOsLocation, iOsRepeat=iOsRepeat,
        iNoteRecNum=iNoteRecNum,
        cFlags=cFlags, cYear=cYear, cMonth=cMonth, cDay=cDay,
        cStartTimePri1=cStartTimePri1, cStartTimePri2=cStartTimePri2,
        iEndDate=iEndDate,
        cEndTimeYear=cEndTimeYear, cEndTimeMonth=cEndTimeMonth,
        cLeadTimeDay=cLeadTimeDay, cLeadTime=cLeadTime,
        cRepeatType=cRepeatType
    )


def _parse_ria(data: bytes, offset: int) -> RepeatInfo:
    """RIA構造体 (12バイト+削除リスト) をRepeatInfoにデコード"""
    ri = RepeatInfo()
    ri.freq        = data[offset]
    days_lo        = data[offset+1]
    weeks_hi       = data[offset+2]
    ri.days        = days_lo + weeks_hi * 256
    month_lo       = data[offset+3]
    month_hi       = data[offset+4]
    ri.month       = month_lo + month_hi * 256
    ri.start_year  = data[offset+5]
    ri.start_month = data[offset+6]
    ri.start_day   = data[offset+7]
    ri.end_year    = data[offset+8]
    ri.end_month   = data[offset+9]
    ri.end_day     = data[offset+10]
    ri.delete_count = data[offset+11]
    if ri.delete_count > 0:
        sz = ri.delete_count * 4
        ri.deleted = bytes(data[offset+12: offset+12+sz])
    return ri


def _data_to_appointment(rec_data: bytes, notes: Dict[int, str]) -> Optional[Appointment]:
    """TYPE_DATAのペイロード → Appointment"""
    if len(rec_data) < NRA_SIZE + 1:
        return None

    nra = _parse_nra(rec_data)
    cFlags = nra['cFlags']

    # 種別判定
    if cFlags >= 128:
        kind = KIND_APPOINTMENT
    elif cFlags >= 32:
        kind = KIND_EVENT
    elif cFlags >= 16:
        kind = KIND_TODO
    else:
        return None  # 未知種別

    appt = Appointment()
    appt.flags = cFlags
    appt.kind = kind
    appt.repeat_type = nra['cRepeatType']
    appt.note_rec_num = nra['iNoteRecNum']
    appt.consec_days = nra['iEndDate']

    appt.start_year  = nra['cYear']
    appt.start_month = nra['cMonth']
    appt.start_day   = nra['cDay']

    # NOTE
    if nra['iNoteRecNum'] != -1 and nra['iNoteRecNum'] in notes:
        raw_note = notes[nra['iNoteRecNum']]
        # \r\n → \n正規化
        appt.note = raw_note.replace('\r\n', '\n').replace('\r', '\n')
    else:
        appt.note = ""

    # Description (offset 27から)
    appt.description = _read_cstring(rec_data, 27)

    # Category
    os_cat = nra['iOsCategory']
    if os_cat < len(rec_data):
        appt.category = _read_cstring(rec_data, os_cat)

    # Location
    os_loc = nra['iOsLocation']
    if os_loc < len(rec_data):
        appt.location = _read_cstring(rec_data, os_loc)

    # Repeat info
    if nra['cRepeatType'] > REPEAT_NONE:
        os_rep = nra['iOsRepeat']
        if os_rep + 12 <= len(rec_data):
            appt.repeat = _parse_ria(rec_data, os_rep)

    if kind == KIND_TODO:
        # ToDoはStartTimePri1/2がPriority
        appt.priority = chr(nra['cStartTimePri1']) if 0x20 <= nra['cStartTimePri1'] <= 0x7E else "1"
        # Complete Date
        appt.complete_year  = nra['cEndTimeYear']
        appt.complete_month = nra['cEndTimeMonth']
        appt.complete_day   = nra['cLeadTimeDay']
        appt.lead_time = nra['cLeadTime']
    else:
        # Appointment/Event
        appt.start_time = _decode_time(nra['cStartTimePri1'], nra['cStartTimePri2'])
        appt.end_time   = _decode_time(nra['cEndTimeYear'], nra['cEndTimeMonth'])
        appt.lead_time_day = nra['cLeadTimeDay']
        appt.lead_time     = nra['cLeadTime']

    return appt


class ADBFile:
    """ADBファイルの読み書きを管理するクラス"""

    def __init__(self):
        self.header_records: List[Tuple[int, int, int, bytes]] = []
        # (cType, cStatus, iRecord, payload_bytes)
        self.appointments: List[Appointment] = []
        self.dbhdr_bytes: bytes = b""   # DBHDRの生バイト (再書き込み用)

    @classmethod
    def read(cls, path: str) -> 'ADBFile':
        obj = cls()
        with open(path, 'rb') as f:
            magic = f.read(4)
            if magic != ADB_MAGIC:
                raise ValueError(f"Not a valid ADB file (magic={magic!r})")

            # DBヘッダ読み込み (25バイト, RHDR_SIZE=6を含む)
            dbhdr_raw = f.read(DBHDR_SIZE)
            if len(dbhdr_raw) < DBHDR_SIZE:
                raise ValueError("ADB file too short (DBHDR)")

            obj.dbhdr_bytes = dbhdr_raw
            # cFileType はオフセット6+2=8バイト目 (RHDR 6byte + iReleaseNum 2byte)
            file_type = chr(dbhdr_raw[8])
            if file_type not in ('2',):
                raise ValueError(f"Unsupported file type: {file_type!r} (only '2'=Appointment supported)")

            num_records = struct.unpack_from('<H', dbhdr_raw, 12)[0]

            # レコードを2パスで読む
            # Pass1: 全レコードを位置ごとにバッファ
            raw_records: List[Tuple[int, int, int, int, bytes]] = []
            # (offset, cType, cStatus, iRecord, payload)

            while True:
                hdr_start = f.tell()
                hdr = f.read(RHDR_SIZE)
                if len(hdr) < RHDR_SIZE:
                    break
                cType   = hdr[0]
                cStatus = hdr[1]
                iLength = struct.unpack_from('<H', hdr, 2)[0]
                iRecord = struct.unpack_from('<h', hdr, 4)[0]

                payload_size = iLength - RHDR_SIZE
                if payload_size < 0:
                    break
                payload = f.read(payload_size)

                raw_records.append((hdr_start, cType, cStatus, iRecord, payload))

                if cType == TYPE_LOOKUPTABLE:
                    break

            # Pass1.5: NOTEレコードをrecord番号→テキストにマップ
            notes: Dict[int, str] = {}
            for _, cType, _, iRecord, payload in raw_records:
                if cType == TYPE_NOTE:
                    try:
                        text = payload.decode('cp932', errors='replace')
                    except Exception:
                        text = payload.decode('latin-1', errors='replace')
                    notes[iRecord] = text

            # Pass2: DATAレコード → Appointment変換、それ以外はheader_recordsへ
            data_count = 0
            for _, cType, cStatus, iRecord, payload in raw_records:
                if cType == TYPE_DATA:
                    appt = _data_to_appointment(payload, notes)
                    if appt is not None:
                        appt.record_num = data_count
                        obj.appointments.append(appt)
                    data_count += 1
                elif cType in (TYPE_NOTE, TYPE_LOOKUPTABLE, TYPE_VIEWPTTABLE):
                    pass  # 再構築時に生成
                else:
                    obj.header_records.append((cType, cStatus, iRecord, payload))

        return obj

    def write(self, path: str, merge: bool = False,
              new_appointments: Optional[List[Appointment]] = None):
        """
        ADBファイルを書き出す。
        merge=True: 既存データを引き継がず new_appointments のみ
        merge=False: self.appointments + new_appointments
        """
        out_appts = []
        if not merge:
            out_appts.extend(self.appointments)
        if new_appointments:
            out_appts.extend(new_appointments)

        # HP 200LX は同一日付のレコードを iPrev/iNextRecNum の連結リストで管理する。
        # apptout.exe と同様に日付順・時刻順にソートしてから書き出す。
        # 全日イベント (start_time=None) は同日の先頭に置く。
        def _sort_key(a: Appointment):
            return (
                a.start_year, a.start_month, a.start_day,
                0 if a.start_time is None else 1,
                a.start_time if a.start_time is not None else 0,
            )
        out_appts.sort(key=_sort_key)

        # ノート番号を事前割り当て (ソート後順序)
        # NOTE の iRecord は DATA の iRecord と重複しないよう len(out_appts) 以降から割り当てる。
        # DATA iRecord は 0..N-1、NOTE iRecord は N.. とすることで
        # HP 200LX が iNoteRecNum を辿る際に DATA レコードと誤認識しない。
        note_assignments: List[int] = []
        note_num = len(out_appts)
        for appt in out_appts:
            if appt.note:
                note_assignments.append(note_num)
                note_num += 1
            else:
                note_assignments.append(-1)

        # 同一日付グループ内の iPrev/iNextRecNum を構築
        # data_num = 0-based index in out_appts (ソート後)
        prev_recs = [-1] * len(out_appts)
        next_recs = [-1] * len(out_appts)
        i = 0
        while i < len(out_appts):
            a = out_appts[i]
            j = i + 1
            while j < len(out_appts):
                b = out_appts[j]
                if (b.start_year, b.start_month, b.start_day) == \
                   (a.start_year, a.start_month, a.start_day):
                    j += 1
                else:
                    break
            # i..j-1 が同一日付グループ
            for k in range(i, j):
                if k > i:
                    prev_recs[k] = k - 1
                if k < j - 1:
                    next_recs[k] = k + 1
            i = j

        # TYPE_14 レコードを他のヘッダレコードと分離する。
        # TYPE_14[1] は日付→iRecord のインデックステーブルで、データ書き込み後に
        # 再構築する必要がある。TYPE_14[0] はカウントフィールドを更新する。
        non_type14_headers = [(cType, cStatus, iRecord, payload)
                              for (cType, cStatus, iRecord, payload) in self.header_records
                              if cType != TYPE_USERTYPE14]
        type14_map = {iRecord: (cStatus, payload)
                      for (cType, cStatus, iRecord, payload) in self.header_records
                      if cType == TYPE_USERTYPE14}

        # TYPE_14[1]: 日付→iRecord インデックステーブルを構築
        # 各ユニーク日付の先頭レコード (iPrev=-1) について 5 バイトエントリを作成:
        #   [cYear, cMonth, cDay, iRec_lo, iRec_hi]
        # エントリは日付順（= data_num 順）に並べ、末尾に終端 5a0000ffff を付ける。
        date_index_entries: List[bytes] = []
        for data_num, appt in enumerate(out_appts):
            if prev_recs[data_num] == -1 and appt.has_date:
                date_index_entries.append(bytes([
                    appt.start_year  & 0xFF,
                    appt.start_month & 0xFF,
                    appt.start_day   & 0xFF,
                    data_num & 0xFF,
                    (data_num >> 8) & 0xFF,
                ]))
        type14_1_payload = b''.join(date_index_entries) + bytes([0x5A, 0x00, 0x00, 0xFF, 0xFF])
        n_unique_dates = len(date_index_entries)

        # TYPE_14[0]: bytes[19:21] (LE short) にユニーク日付数を書き込む
        if 0 in type14_map:
            cStatus_t14_0, payload_t14_0 = type14_map[0]
            pa = bytearray(payload_t14_0)
            if len(pa) >= 21:
                struct.pack_into('<H', pa, 19, n_unique_dates)
            type14_0_payload = bytes(pa)
        else:
            cStatus_t14_0, type14_0_payload = 2, b''

        if 1 in type14_map:
            cStatus_t14_1, _ = type14_map[1]
        else:
            cStatus_t14_1 = 2

        with open(path, 'wb') as f:
            # マジック
            f.write(ADB_MAGIC)

            # DBHDR: まず書き込み位置を記録してから書き出す
            # (修正) DBHDR も seek_info に追加する必要がある
            dbhdr_pos = f.tell()  # = 4 (magic直後)
            dbhdr = bytearray(self.dbhdr_bytes)
            f.write(bytes(dbhdr))

            # SeekInfo: (cType, iRecord, iLength, file_offset) per record
            # iLength = RHDR_SIZE + payload_size (C言語版と同一定義)
            seek_info: List[Tuple[int, int, int, int]] = []

            # (修正) DBHDR を seek_info に追加
            seek_info.append((TYPE_DBHEADER, 0, DBHDR_SIZE, dbhdr_pos))

            def write_record(cType, cStatus, iRecord, payload):
                pos = f.tell()
                iLength = RHDR_SIZE + len(payload)
                hdr = struct.pack('<BBHh', cType, cStatus, iLength, iRecord)
                f.write(hdr)
                f.write(payload)
                seek_info.append((cType, iRecord, iLength, pos))

            # ヘッダレコード群 (FieldDef等) をコピー (TYPE_14 を除く)
            for cType, cStatus, iRecord, payload in non_type14_headers:
                write_record(cType, cStatus, iRecord, payload)

            # アポイントメントデータを書き出す (日付・時刻ソート済み)
            for data_num, appt in enumerate(out_appts):
                n_rec = note_assignments[data_num]
                payload, note_payload = _appointment_to_data(
                    appt, n_rec,
                    prev_rec=prev_recs[data_num],
                    next_rec=next_recs[data_num],
                )
                write_record(TYPE_DATA, 2, data_num, payload)
                if appt.note:
                    write_record(TYPE_NOTE, 2, n_rec, note_payload)

            # TYPE_14 を DATA/NOTE の後ろに書き出す (ok_dos/apptout.exe と同じ順序)
            # iRecord=0 → 設定レコード、iRecord=1 → 日付インデックステーブル
            if type14_0_payload:
                write_record(TYPE_USERTYPE14, cStatus_t14_0, 0, type14_0_payload)
            write_record(TYPE_USERTYPE14, cStatus_t14_1, 1, type14_1_payload)

            # ViewPointTable (空)
            vpt_pos = f.tell()
            vpt_hdr = struct.pack('<BBHh', TYPE_VIEWPTTABLE, 2, 6, 0)
            f.write(vpt_hdr)
            seek_info.append((TYPE_VIEWPTTABLE, 0, 6, vpt_pos))

            # LookupTable
            # (修正1) iLength = N*8+6 のみ。type-count テーブル(64バイト)は含めない
            # (修正2) LUT 自身は seek_info に追加しない（C言語版と同様）
            lut_pos = f.tell()
            n_index = len(seek_info)  # LUT を除いた全レコード数
            lut_iLength = n_index * 8 + RHDR_SIZE
            lut_hdr = struct.pack('<BBHh', TYPE_LOOKUPTABLE, 2, lut_iLength, 0)
            f.write(lut_hdr)

            # (修正3) インデックスエントリは cType 順にソートして書く
            seek_info_sorted = sorted(seek_info, key=lambda x: x[0])
            for cType, iRecord, iLength, offset in seek_info_sorted:
                entry = bytearray(8)
                entry[0] = iLength & 0xFF
                entry[1] = (iLength >> 8) & 0xFF
                entry[2] = 0xFF
                entry[3] = 0xFF
                entry[4] = 0x00 if offset else 0xC0
                entry[5] = offset & 0xFF
                entry[6] = (offset >> 8) & 0xFF
                entry[7] = (offset >> 16) & 0xFF
                f.write(bytes(entry))

            # 32種類の type別開始インデックステーブル (cType 順ソート後の累積開始位置)
            counts_by_type = [0] * 32
            for cType, _, _, _ in seek_info_sorted:
                if 0 <= cType < 32:
                    counts_by_type[cType] += 1
            running = 0
            for i in range(32):
                f.write(struct.pack('<H', running))
                running += counts_by_type[i]

            # (修正4) DBHDR の iNumRecords = LUT インデックスの全エントリ数 (= n_index)
            #         iLookUpSeek = LUT レコードの先頭オフセット (lut_pos)
            f.seek(4 + 12)  # magic(4) + DBHDR先頭からiNumRecordsまでのオフセット12
            f.write(struct.pack('<H', n_index))
            f.write(struct.pack('<I', lut_pos))


# ---------------------------------------------------------------------------
# ADB バイナリ書き込み補助
# ---------------------------------------------------------------------------

def _encode_time(minutes: Optional[int]) -> Tuple[int, int]:
    """分 → (cPri1, cPri2). NONE時は (0x00, 0x00)"""
    if minutes is None:
        return (0x00, 0x00)
    return (minutes & 0xFF, (minutes >> 8) & 0xFF)


def _appointment_to_data(appt: Appointment, note_rec_num: int,
                         prev_rec: int = -1, next_rec: int = -1) -> Tuple[bytes, bytes]:
    """
    AppointmentをTYPE_DATAのペイロードとTYPE_NOTEのペイロードに変換。
    Returns (data_payload, note_payload)
    """
    desc_b  = (appt.description or "").encode('cp932', errors='replace') + b'\x00'
    cat_b   = (appt.category or "").encode('cp932', errors='replace') + b'\x00'
    loc_b   = (appt.location or "").encode('cp932', errors='replace') + b'\x00'

    # リピート情報
    repeat_b = b""
    if appt.repeat_type > REPEAT_NONE and appt.repeat is not None:
        ri = appt.repeat
        repeat_b = bytes([
            ri.freq & 0xFF,
            ri.day_bits,
            ri.week_bits,
            ri.month & 0xFF,
            (ri.month >> 8) & 0xFF,
            ri.start_year & 0xFF,
            ri.start_month & 0xFF,
            ri.start_day & 0xFF,
            ri.end_year & 0xFF,
            ri.end_month & 0xFF,
            ri.end_day & 0xFF,
            ri.delete_count & 0xFF,
        ])
        if ri.deleted:
            repeat_b += ri.deleted

    # NRAオフセット計算 (NRA_SIZE=27 から desc_b が始まる)
    os_category = NRA_SIZE + len(desc_b)
    os_location  = os_category + len(cat_b)
    os_repeat    = os_location + len(loc_b)
    total_length = os_repeat + len(repeat_b)

    # NRA構造体を構築
    nra = bytearray(NRA_SIZE)
    struct.pack_into('<H', nra, 0, total_length)           # iLength
    struct.pack_into('<H', nra, 2, os_category)            # iOsCategory
    struct.pack_into('<H', nra, 4, os_location)            # iOsLocation
    struct.pack_into('<H', nra, 6, os_repeat)              # iOsRepeat
    struct.pack_into('<h', nra, 8, note_rec_num)           # iNoteRecNum
    struct.pack_into('<h', nra, 10, prev_rec)               # iPrevRecNum
    struct.pack_into('<h', nra, 12, next_rec)               # iNextRecNum
    nra[14] = appt.flags                                   # cFlags

    nra[15] = appt.start_year & 0xFF
    nra[16] = appt.start_month & 0xFF
    nra[17] = appt.start_day & 0xFF

    struct.pack_into('<H', nra, 20, appt.consec_days)      # iEndDate
    nra[26] = appt.repeat_type                             # cRepeatType

    if appt.kind == KIND_TODO:
        # Priority
        pri = appt.priority[0] if appt.priority else '1'
        nra[18] = ord(pri)
        nra[19] = 0
        # Complete date
        nra[22] = appt.complete_year & 0xFF
        nra[23] = appt.complete_month & 0xFF
        nra[24] = appt.complete_day & 0xFF
        nra[25] = 0xFF & appt.lead_time
    else:
        # Start/End time
        st1, st2 = _encode_time(appt.start_time)
        et1, et2 = _encode_time(appt.end_time)
        nra[18] = st1; nra[19] = st2
        nra[22] = et1; nra[23] = et2
        nra[24] = appt.lead_time_day & 0xFF
        nra[25] = appt.lead_time & 0xFF

    payload = bytes(nra) + desc_b + cat_b + loc_b + repeat_b

    # ノートペイロード
    note_payload = b""
    if appt.note:
        note_text = appt.note.replace('\n', '\r\n')
        note_payload = note_text.encode('cp932', errors='replace')

    return payload, note_payload


# ---------------------------------------------------------------------------
# CSV 出力
# ---------------------------------------------------------------------------

def _repeat_day_str(days: int) -> str:
    """days (day_bits + week_bits*256) → 文字列"""
    week_bits = (days >> 8) & 0xFF
    day_bits  = days & 0xFF
    if week_bits == 0 and day_bits < 128:
        return str(day_bits)
    parts = []
    nth = week_bits
    if nth:
        parts.append(f"{nth}th")
    if day_bits & 0x01: parts.append("MON")
    if day_bits & 0x02: parts.append("TUE")
    if day_bits & 0x04: parts.append("WED")
    if day_bits & 0x08: parts.append("THU")
    if day_bits & 0x10: parts.append("FRI")
    if day_bits & 0x20: parts.append("SAT")
    if day_bits & 0x40: parts.append("SUN")
    return ",".join(parts)


def _repeat_month_str(month: int) -> str:
    """month ビットマップ → 文字列"""
    if month < 256:
        return str(month)
    names = ["JAN","FEB","MAR","APR","MAY","JUN",
             "JUL","AUG","SEP","OCT","NOV","DEC"]
    bits  = [1,2,4,8,16,32,64,128,256,512,2048,1024]
    parts = [n for n, b in zip(names, bits) if month & b]
    return ",".join(parts)


def _fmt_date_raw(y, m, d) -> str:
    if y == 0xFF or y == 255:
        return ""
    return f"{y+1900:04d}/{m+1:02d}/{d+1:02d}"


def _note_encode(note: str, mode: int) -> str:
    """note_mode: 0=そのまま, 1=スペース, 2=\\n文字列(デフォルト)"""
    if mode == 0:
        return note
    elif mode == 1:
        return note.replace('\n', ' ')
    else:  # 2
        return note.replace('\n', '\\n')


def export_csv(appointments: List[Appointment],
               include_appt: bool = True,
               include_event: bool = False,
               include_todo: int = 0,
               note_mode: int = 2,
               date_start: int = 19000101,
               date_end: int = 22000101,
               output=None,
               encoding: str = 'utf-8',
               tab_mode: bool = False):
    """
    CSV/TSV出力。output=Noneなら sys.stdout に書く。
    encoding: 'utf-8' or 'cp932'
    tab_mode: True でタブ区切り・クォートなし出力 (-b 相当)
    """
    if output is None:
        if encoding == 'cp932':
            output = io.TextIOWrapper(sys.stdout.buffer, encoding='cp932', errors='replace')
            close_output = False
        else:
            output = sys.stdout
            close_output = False
    else:
        close_output = False

    if tab_mode:
        # タブ区切り: フィールド内のタブ・改行はスペースに置換してクォートなしで出力
        def _write_row(row):
            fields = [str(f).replace('\t', ' ').replace('\r', '').replace('\n', ' ')
                      for f in row]
            output.write('\t'.join(fields) + '\r\n')
        writer_writerow = _write_row
    else:
        _csv_writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
        writer_writerow = _csv_writer.writerow

    for appt in appointments:
        # 日付フィルタ
        d = appt.start_date_int
        if d > 0 and (d < date_start or d > date_end):
            continue

        # 種別フィルタ
        if appt.kind == KIND_APPOINTMENT:
            if not include_appt:
                continue
            if include_appt == 2 and appt.repeat_type > REPEAT_NONE:
                continue
            if include_appt == 3 and appt.repeat_type == REPEAT_NONE:
                continue
        elif appt.kind == KIND_EVENT:
            if not include_event:
                continue
            if include_event == 2 and appt.repeat_type > REPEAT_NONE:
                continue
            if include_event == 3 and appt.repeat_type == REPEAT_NONE:
                continue
        elif appt.kind == KIND_TODO:
            if not include_todo:
                continue

        note_str = _note_encode(appt.note, note_mode)

        if appt.kind in (KIND_APPOINTMENT, KIND_EVENT):
            # Appointment/Event CSV format
            start_time = f"{appt.start_time//60:02d}:{appt.start_time%60:02d}" if appt.start_time is not None else "NONE"
            end_time   = f"{appt.end_time//60:02d}:{appt.end_time%60:02d}"     if appt.end_time   is not None else "NONE"

            row = [
                appt.description,
                appt.start_date_str,
                start_time,
                end_time,
                appt.location,
                str(appt.consec_days),
                note_str,
            ]
            if appt.repeat_type > REPEAT_NONE and appt.repeat:
                ri = appt.repeat
                days_s = _repeat_day_str(ri.days)
                month_s = _repeat_month_str(ri.month)
                start_s = _fmt_date_raw(ri.start_year, ri.start_month, ri.start_day)
                end_s   = _fmt_date_raw(ri.end_year, ri.end_month, ri.end_day)
                row += [str(ri.freq), days_s, month_s, start_s, end_s, str(ri.delete_count)]

            writer_writerow(row)

        elif appt.kind == KIND_TODO:
            complete_s = _fmt_date_raw(appt.complete_year, appt.complete_month, appt.complete_day)

            if include_todo == 4:
                # Appt/Event形式にあわせて出力
                row = [
                    appt.description,
                    appt.start_date_str,
                    "", "", "",
                    str(appt.consec_days),
                    note_str,
                ]
            else:
                row = [
                    appt.description,
                    appt.start_date_str,
                    str(appt.consec_days),
                    complete_s,
                    appt.priority,
                    note_str,
                ]
            if appt.repeat_type > REPEAT_NONE and appt.repeat:
                ri = appt.repeat
                days_s = _repeat_day_str(ri.days)
                month_s = _repeat_month_str(ri.month)
                start_s = _fmt_date_raw(ri.start_year, ri.start_month, ri.start_day)
                end_s   = _fmt_date_raw(ri.end_year, ri.end_month, ri.end_day)
                row += [str(ri.freq), days_s, month_s, start_s, end_s, str(ri.delete_count)]

            writer_writerow(row)


# ---------------------------------------------------------------------------
# CSV 入力
# ---------------------------------------------------------------------------

def _repeat_day_parse(s: str) -> int:
    """repeat day文字列 → int (days + weeks*256)"""
    s = s.strip()
    if not s:
        return 0
    if len(s) < 3:
        return int(s) if s.isdigit() else 0
    nth = 0
    if 'th' in s:
        import re
        m = re.match(r'(\d+)th', s)
        if m:
            nth = int(m.group(1))
    day_bits = 0
    if 'MON' in s: day_bits |= 0x01
    if 'TUE' in s: day_bits |= 0x02
    if 'WED' in s: day_bits |= 0x04
    if 'THU' in s: day_bits |= 0x08
    if 'FRI' in s: day_bits |= 0x10
    if 'SAT' in s: day_bits |= 0x20
    if 'SUN' in s: day_bits |= 0x40
    return day_bits + nth * 256


def _repeat_month_parse(s: str) -> int:
    """repeat month文字列 → int (ビットマップ)"""
    s = s.strip()
    if not s:
        return 0
    if len(s) < 2:
        return int(s) if s.isdigit() else 0
    m = 0
    if 'JAN' in s: m |= 1
    if 'FEB' in s: m |= 2
    if 'MAR' in s: m |= 4
    if 'APR' in s: m |= 8
    if 'MAY' in s: m |= 16
    if 'JUN' in s: m |= 32
    if 'JUL' in s: m |= 64
    if 'AUG' in s: m |= 128
    if 'SEP' in s: m |= 256
    if 'OCT' in s: m |= 512
    if 'DEC' in s: m |= 1024
    if 'NOV' in s: m |= 2048
    return m


def _parse_date_str(s: str) -> Tuple[int, int, int]:
    """YYYY/MM/DD → (year-1900, month-1, day-1). 空なら (0xFF,0xFF,0xFF)"""
    s = s.strip()
    if not s:
        return (0xFF, 0xFF, 0xFF)
    parts = s.replace('-', '/').split('/')
    if len(parts) != 3:
        return (0xFF, 0xFF, 0xFF)
    y = int(parts[0]) - 1900
    m = int(parts[1]) - 1
    d = int(parts[2]) - 1
    return (y & 0xFF, m & 0xFF, d & 0xFF)


def _parse_time_str(s: str) -> Optional[int]:
    """HH:MM または NONE → 分 (None=NONE)"""
    s = s.strip().upper()
    if not s or s == 'NONE':
        return None
    parts = s.split(':')
    if len(parts) != 2:
        return None
    return int(parts[0]) * 60 + int(parts[1])


def _determine_repeat_type(freq: int, days: int, month: int) -> int:
    if freq == 0 and days == 0 and month == 0:
        return REPEAT_NONE
    if freq != 0 and days == 0 and month == 0:
        return REPEAT_DAILY
    if freq != 0 and days >= 128 and month == 0:
        return REPEAT_WEEKLY
    if freq != 0 and 0 < days < 128 and month == 0:
        return REPEAT_MONTHLY
    if freq != 0 and month != 0:
        return REPEAT_YEARLY
    return REPEAT_CUSTOM


def import_csv(path: str, kind: str = 'appt',
               encoding: str = 'utf-8',
               alarm: int = 7) -> List[Appointment]:
    """
    CSVファイルを読み込み Appointment リストを返す。
    kind: 'appt', 'event', 'todo'
    alarm: -r オプション相当 (0-7)
    """
    results = []

    if kind == 'appt':
        base_kind = KIND_APPOINTMENT
        base_flags = 128 + alarm
    elif kind == 'event':
        base_kind = KIND_EVENT
        base_flags = 38   # MonthView=ON, WeekView=ON
    else:
        base_kind = KIND_TODO
        base_flags = 20   # CarryForward=ON

    with open(path, 'r', encoding=encoding, errors='replace', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            n = len(row)

            appt = Appointment()
            appt.kind  = base_kind
            appt.flags = base_flags

            if base_kind in (KIND_APPOINTMENT, KIND_EVENT):
                # 列: Description, StartDate, StartTime, EndTime, Location,
                #     ConsecDays, Note [, Freq, Days, Months, RpStart, RpEnd, DelCount]
                appt.description = row[0] if n > 0 else ""
                if n > 1:
                    y, m, d = _parse_date_str(row[1])
                    appt.start_year, appt.start_month, appt.start_day = y, m, d
                appt.start_time = _parse_time_str(row[2]) if n > 2 else None
                appt.end_time   = _parse_time_str(row[3]) if n > 3 else None
                appt.location   = (row[4].strip() if n > 4 else "")
                appt.consec_days = int(row[5]) if n > 5 and row[5].strip() else 0
                note_raw = row[6] if n > 6 else ""
                appt.note = note_raw.replace('\\n', '\n')

                if n >= 13:  # 繰り返しあり
                    ri = RepeatInfo()
                    ri.freq  = int(row[7]) if row[7].strip() else 0
                    ri.days  = _repeat_day_parse(row[8]) if n > 8 else 0
                    ri.month = _repeat_month_parse(row[9]) if n > 9 else 0
                    if n > 10:
                        ri.start_year, ri.start_month, ri.start_day = _parse_date_str(row[10])
                    if n > 11:
                        ri.end_year, ri.end_month, ri.end_day = _parse_date_str(row[11])
                    ri.delete_count = int(row[12]) if n > 12 and row[12].strip() else 0
                    appt.repeat = ri
                    appt.repeat_type = _determine_repeat_type(ri.freq, ri.days, ri.month)
                else:
                    appt.repeat_type = REPEAT_NONE

            else:  # ToDo
                # 列: Description, StartDate, ConsecDays, CompleteDate, Priority,
                #     Note [, Freq, Days, Months, RpStart, RpEnd, DelCount]
                appt.description = row[0] if n > 0 else ""
                if n > 1:
                    y, m, d = _parse_date_str(row[1])
                    appt.start_year, appt.start_month, appt.start_day = y, m, d
                appt.consec_days = int(row[2]) if n > 2 and row[2].strip() else 0
                if n > 3:
                    cy, cm, cd = _parse_date_str(row[3])
                    appt.complete_year, appt.complete_month, appt.complete_day = cy, cm, cd
                appt.priority = row[4][0] if n > 4 and row[4] else "1"
                note_raw = row[5] if n > 5 else ""
                appt.note = note_raw.replace('\\n', '\n')

                if n >= 12:
                    ri = RepeatInfo()
                    ri.freq  = int(row[6]) if row[6].strip() else 0
                    ri.days  = _repeat_day_parse(row[7]) if n > 7 else 0
                    ri.month = _repeat_month_parse(row[8]) if n > 8 else 0
                    if n > 9:
                        ri.start_year, ri.start_month, ri.start_day = _parse_date_str(row[9])
                    if n > 10:
                        ri.end_year, ri.end_month, ri.end_day = _parse_date_str(row[10])
                    ri.delete_count = int(row[11]) if n > 11 and row[11].strip() else 0
                    appt.repeat = ri
                    appt.repeat_type = _determine_repeat_type(ri.freq, ri.days, ri.month)
                else:
                    appt.repeat_type = REPEAT_NONE

            results.append(appt)

    return results


# ---------------------------------------------------------------------------
# ICS 入力
# ---------------------------------------------------------------------------

def _ics_unfold(text: str) -> str:
    """RFC5545 行折り畳み(CRLF + SPACE/TAB)を展開"""
    return text.replace('\r\n ', '').replace('\r\n\t', '').replace('\n ', '').replace('\n\t', '')


def _ics_parse_dt(value: str, tzid: Optional[str] = None) -> Optional[datetime]:
    """DTSTART/DTEND の値をdatetimeに変換"""
    value = value.strip()
    try:
        if value.endswith('Z'):
            dt = datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            dt = dt.replace(tzinfo=timezone.utc)
            # JST (UTC+9) に変換
            dt = dt.astimezone(timezone(timedelta(hours=9)))
            return dt
        elif 'T' in value:
            return datetime.strptime(value, '%Y%m%dT%H%M%S')
        else:
            dt = datetime.strptime(value, '%Y%m%d')
            return dt
    except ValueError:
        return None


def _rrule_to_repeat(rrule: str, dtstart: Optional[datetime]) -> Tuple[int, Optional[RepeatInfo]]:
    """RRULE文字列 → (repeat_type, RepeatInfo)"""
    params = {}
    for part in rrule.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[k.strip()] = v.strip()

    freq_str = params.get('FREQ', '')
    interval = int(params.get('INTERVAL', '1'))
    until_str = params.get('UNTIL', '')
    byday = params.get('BYDAY', '')
    bymonth = params.get('BYMONTH', '')
    bysetpos = params.get('BYSETPOS', '')

    ri = RepeatInfo()
    ri.freq = interval

    repeat_type = REPEAT_NONE

    if freq_str == 'DAILY':
        repeat_type = REPEAT_DAILY

    elif freq_str == 'WEEKLY':
        repeat_type = REPEAT_WEEKLY
        day_map = {'MO': 0x01, 'TU': 0x02, 'WE': 0x04,
                   'TH': 0x08, 'FR': 0x10, 'SA': 0x20, 'SU': 0x40}
        bits = 0
        for d in byday.split(','):
            d = d.strip()
            for k, v in day_map.items():
                if d.endswith(k):
                    bits |= v
        ri.days = bits + 128  # 128 = weekly フラグ

    elif freq_str == 'MONTHLY':
        repeat_type = REPEAT_MONTHLY
        if byday:
            # Nth weekday
            import re
            m = re.match(r'(-?\d+)([A-Z]{2})', byday.strip())
            if m:
                nth = int(m.group(1))
                day_map = {'MO': 0x01, 'TU': 0x02, 'WE': 0x04,
                           'TH': 0x08, 'FR': 0x10, 'SA': 0x20, 'SU': 0x40}
                bits = day_map.get(m.group(2), 0)
                ri.days = bits + nth * 256

    elif freq_str == 'YEARLY':
        repeat_type = REPEAT_YEARLY
        month_bits_map = [1,2,4,8,16,32,64,128,256,512,2048,1024]
        bits = 0
        for mn in bymonth.split(','):
            mn = mn.strip()
            if mn.isdigit():
                idx = int(mn) - 1
                if 0 <= idx < 12:
                    bits |= month_bits_map[idx]
        ri.month = bits

    # Until (繰り返し終了日)
    if until_str:
        until_dt = _ics_parse_dt(until_str)
        if until_dt:
            ri.end_year  = (until_dt.year - 1900) & 0xFF
            ri.end_month = (until_dt.month - 1) & 0xFF
            ri.end_day   = (until_dt.day - 1) & 0xFF

    # Start (dtstart)
    if dtstart:
        ri.start_year  = (dtstart.year - 1900) & 0xFF
        ri.start_month = (dtstart.month - 1) & 0xFF
        ri.start_day   = (dtstart.day - 1) & 0xFF

    return repeat_type, ri if repeat_type != REPEAT_NONE else None


def _detect_ics_encoding(path: str) -> str:
    """
    ICS ファイルのエンコーディングを推定する。
    UTF-8 BOM → 'utf-8-sig'
    strict UTF-8 で読めれば → 'utf-8'
    失敗すれば → 'cp932' (Shift-JIS)
    """
    with open(path, 'rb') as f:
        bom = f.read(3)
    if bom == b'\xef\xbb\xbf':
        return 'utf-8-sig'
    try:
        with open(path, 'r', encoding='utf-8', errors='strict') as f:
            f.read()
        return 'utf-8'
    except (UnicodeDecodeError, ValueError):
        return 'cp932'


def import_ics(path: str, alarm: int = 7, date_from: int = 0,
               encoding: Optional[str] = None) -> List[Appointment]:
    """
    ICSファイルを読み込み Appointment リストを返す。
    DTSTART に時刻があれば Appointment、終日なら Event として自動判定する。

    alarm:     アラーム設定 (0-7)
    date_from: この日付 (yyyymmdd int) より前のイベントを除外。0=フィルタなし。
    encoding:  None=自動検出, 'utf-8', 'cp932' など
    """
    if encoding:
        enc = encoding
    else:
        enc = _detect_ics_encoding(path)
        print(f"  ICS encoding detected: {enc}", file=sys.stderr)
    with open(path, 'r', encoding=enc, errors='replace') as f:
        raw = f.read()

    text = _ics_unfold(raw)
    lines = text.splitlines()

    results = []
    in_vevent = False
    props: Dict[str, str] = {}

    for line in lines:
        if line.strip() == 'BEGIN:VEVENT':
            in_vevent = True
            props = {}
            continue
        if line.strip() == 'END:VEVENT':
            in_vevent = False
            appt = _vevent_to_appointment(props, alarm)
            if appt is not None:
                # 日付フィルタ: date_from より前のイベントは除外
                if date_from > 0 and appt.has_date and appt.start_date_int < date_from:
                    continue
                results.append(appt)
            continue
        if in_vevent and ':' in line:
            colon_idx = line.index(':')
            key_part = line[:colon_idx]
            value = line[colon_idx+1:]
            key = key_part.split(';')[0].upper()
            if ';TZID=' in key_part.upper():
                tzid = key_part.split('TZID=')[-1]
                props[key + '_TZID'] = tzid
            props[key] = value

    return results


def _vevent_to_appointment(props: Dict[str, str],
                           alarm: int = 7) -> Optional[Appointment]:
    """
    VEVENTプロパティ辞書 → Appointment。
    DTSTART に時刻が含まれる場合は Appointment (cFlags=128+alarm)、
    終日イベント (VALUE=DATE) の場合は Event (cFlags=38) として登録する。
    """
    appt = Appointment()

    # DTSTART を先に読んで種別を自動判定
    dtstart_raw = props.get('DTSTART', '')
    is_timed = 'T' in dtstart_raw   # 時刻あり=Appointment, なし=Event
    if is_timed:
        appt.kind  = KIND_APPOINTMENT
        appt.flags = 128 + alarm
    else:
        appt.kind  = KIND_EVENT
        appt.flags = 38

    # SUMMARY → description
    appt.description = props.get('SUMMARY', '').strip()

    # LOCATION
    appt.location = props.get('LOCATION', '').strip()

    # DESCRIPTION → note
    desc = props.get('DESCRIPTION', '')
    # ICS DESCRIPTION はバックスラッシュエスケープ
    desc = desc.replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\')
    appt.note = desc

    # DTSTART (dtstart_raw は関数先頭で取得済み)
    dtstart = _ics_parse_dt(dtstart_raw) if dtstart_raw else None

    if dtstart:
        appt.start_year  = (dtstart.year  - 1900) & 0xFF
        appt.start_month = (dtstart.month - 1) & 0xFF
        appt.start_day   = (dtstart.day   - 1) & 0xFF

        if is_timed:
            appt.start_time = dtstart.hour * 60 + dtstart.minute
        else:
            appt.start_time = None  # 終日イベント
    else:
        appt.start_year = appt.start_month = appt.start_day = 0xFF

    # DTEND
    dtend_raw = props.get('DTEND', '')
    dtend = _ics_parse_dt(dtend_raw) if dtend_raw else None

    if dtend and dtstart:
        if 'T' in (dtend_raw or ''):
            appt.end_time = dtend.hour * 60 + dtend.minute
        else:
            appt.end_time = None
        # 終日イベントの複数日: HP 200LX は iEndDate!=0 のレコードを誤判定するため
        # DOS apptout と同様に常に 0 にする。開始日のみの単日イベントとして登録。
        # appt.consec_days は既定値 0 のままにする。

    # RRULE → 繰り返し情報
    rrule = props.get('RRULE', '')
    if rrule:
        rtype, ri = _rrule_to_repeat(rrule, dtstart)
        appt.repeat_type = rtype
        appt.repeat = ri
    else:
        appt.repeat_type = REPEAT_NONE

    # Descriptionが長すぎる場合は切り詰め (HP200LX制限: 51文字)
    # ICS取り込みの場合は制限を緩和 (noteに格納済みのため)
    if len(appt.description.encode('cp932', errors='replace')) > 51:
        # 51バイトに切り詰め (Shift-JIS考慮)
        encoded = appt.description.encode('cp932', errors='replace')[:51]
        appt.description = encoded.decode('cp932', errors='replace')

    return appt


# ---------------------------------------------------------------------------
# 重複チェック
# ---------------------------------------------------------------------------

def _appt_key(appt: Appointment) -> tuple:
    """重複判定キー: (description, date, start_time)"""
    return (
        appt.description.strip(),
        appt.start_year,
        appt.start_month,
        appt.start_day,
        appt.start_time,
    )


def deduplicate(existing: List[Appointment],
                new_appts: List[Appointment]) -> Tuple[List[Appointment], int]:
    """
    existing に既に存在するものを new_appts から除外して返す。
    一致判定: (description, 開始日, 開始時刻)
    戻り値: (フィルタ後リスト, スキップ件数)
    """
    existing_keys = {_appt_key(a) for a in existing}
    filtered = []
    skipped = 0
    for a in new_appts:
        if _appt_key(a) in existing_keys:
            skipped += 1
        else:
            filtered.append(a)
    return filtered, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_yymmdd(s: str) -> int:
    """YYMMDD または YYYYMMDD → yyyymmdd int"""
    s = s.strip()
    if len(s) == 6:
        n = int(s)
        if n < 100:
            return 19000101
        elif n < 900000:
            return n + 20000000
        else:
            return n + 19000000
    elif len(s) == 8:
        return int(s)
    return 19000101


def main():
    from datetime import date as _date
    parser = argparse.ArgumentParser(
        prog='appt_out.py',
        description='HP 200LX *.adb Appointment Book 入出力ツール'
    )
    parser.add_argument('-x', dest='input_adb', metavar='FILE',
                        required=True, help='入力ADBファイル (必須)')

    # 入力: ICS と CSV は専用オプションで分離
    parser.add_argument('--ics', dest='input_ics', metavar='FILE',
                        help='取り込む ICS ファイル (終日→Event, 時間あり→Appointment 自動判定)')
    parser.add_argument('--ics-from', dest='ics_from', metavar='YYMMDD', default=None,
                        help='ICS 取り込み開始日 (デフォルト: 前年1/1。0=フィルタなし)')
    parser.add_argument('-i', dest='input_csv', metavar='FILE',
                        help='取り込む CSV ファイル (要 -a/-e/-t)')

    parser.add_argument('-o', dest='output_adb', metavar='FILE',
                        help='出力ADBファイル')

    # 種別フィルタ (CSV 取り込み / ADB→CSV 出力に使用)
    kind_grp = parser.add_mutually_exclusive_group()
    kind_grp.add_argument('-a', dest='appt', nargs='?', const='1', default=None,
                          metavar='N', help='Appointment 出力/CSV取込 (0-3)')
    kind_grp.add_argument('-e', dest='event', nargs='?', const='1', default=None,
                          metavar='N', help='Event 出力/CSV取込 (0-3)')
    kind_grp.add_argument('-t', dest='todo', nargs='?', const='1', default=None,
                          metavar='N', help='ToDo 出力 (0-4)')

    parser.add_argument('-m', dest='merge', action='store_true',
                        help='新規作成モード (既存データをコピーしない)')

    # dedup: CSV は opt-in (--dedup)、ICS は opt-out (--no-dedup)
    dedup_grp = parser.add_mutually_exclusive_group()
    dedup_grp.add_argument('--dedup', dest='dedup', action='store_true', default=False,
                           help='CSV 取り込み時の重複スキップを有効化')
    dedup_grp.add_argument('--no-dedup', dest='no_dedup', action='store_true', default=False,
                           help='ICS 取り込み時の重複スキップを無効化 (デフォルトは ON)')

    parser.add_argument('-n', dest='note_mode', nargs='?', const='2', default='2',
                        metavar='N', help='NOTE 改行処理 0=そのまま 1=スペース 2=\\n (デフォルト)')
    parser.add_argument('-r', dest='alarm', nargs='?', const='7', default='7',
                        metavar='N', help='アラーム/月表示/週表示設定 0-7 (デフォルト: 7)')
    parser.add_argument('-g', dest='date_start', metavar='YYMMDD',
                        help='CSV 出力開始日 (例: 260101)')
    parser.add_argument('-f', dest='date_end', metavar='YYMMDD',
                        help='CSV 出力終了日 (例: 261231)')
    parser.add_argument('-b', dest='tab_mode', action='store_true',
                        help='タブ区切り出力 (デフォルト: CSV)')
    parser.add_argument('--csv-encoding', dest='csv_encoding',
                        default='utf-8', choices=['utf-8', 'cp932', 'shift-jis'],
                        help='CSV 入出力エンコーディング (デフォルト: utf-8)')
    parser.add_argument('--ics-encoding', dest='ics_encoding',
                        default=None, metavar='ENC',
                        help='ICS 入力エンコーディング (デフォルト: 自動検出)')
    parser.add_argument('--silent', '-s', dest='silent', nargs='?', const='1',
                        default='0', metavar='N',
                        help='出力抑制 0=通常 1=進捗非表示 2=全て抑制')

    args = parser.parse_args()

    # --ics と -i の同時指定はエラー
    if args.input_ics and args.input_csv:
        parser.error('--ics と -i は同時に指定できません')

    # エンコーディング正規化
    csv_enc = 'cp932' if args.csv_encoding in ('cp932', 'shift-jis') else 'utf-8'

    # 種別フラグ解析
    include_appt  = int(args.appt)  if args.appt  is not None else 0
    include_event = int(args.event) if args.event is not None else 0
    include_todo  = int(args.todo)  if args.todo  is not None else 0

    # 出力モード指定なし・入力なし → Appointment 出力をデフォルトとする
    if args.appt is None and args.event is None and args.todo is None:
        if args.input_ics is None and args.input_csv is None:
            include_appt = 1

    note_mode = int(args.note_mode or '2')
    alarm_val = int(args.alarm or '7')
    silent    = int(args.silent or '0')

    date_start = _parse_yymmdd(args.date_start) if args.date_start else 19000101
    date_end   = _parse_yymmdd(args.date_end)   if args.date_end   else 22000101

    # ICS 取り込み開始日: デフォルト = 前年 1/1
    if args.ics_from is not None:
        ics_from_val = _parse_yymmdd(args.ics_from) if args.ics_from != '0' else 0
    else:
        last_year = _date.today().year - 1
        ics_from_val = last_year * 10000 + 101  # 前年 1/1

    # ADB読み込み
    if silent < 2:
        print(f"Reading {args.input_adb} ...", file=sys.stderr)

    try:
        adb = ADBFile.read(args.input_adb)
    except Exception as e:
        print(f"Error reading ADB: {e}", file=sys.stderr)
        sys.exit(1)

    if silent < 2:
        print(f"  {len(adb.appointments)} records loaded.", file=sys.stderr)

    # ICS 取り込み
    new_appts: List[Appointment] = []
    if args.input_ics:
        if silent < 2:
            print(f"Importing {args.input_ics} (ICS, auto-detect kind) ...", file=sys.stderr)
            if ics_from_val > 0:
                print(f"  Date filter: {ics_from_val} 以降", file=sys.stderr)
        try:
            new_appts = import_ics(args.input_ics, alarm=alarm_val,
                                   date_from=ics_from_val,
                                   encoding=args.ics_encoding)
        except Exception as e:
            print(f"Error importing ICS: {e}", file=sys.stderr)
            sys.exit(1)
        if silent < 2:
            print(f"  {len(new_appts)} records imported.", file=sys.stderr)

        # ICS: dedup はデフォルト ON (--no-dedup で無効化)
        if not args.no_dedup and not args.merge:
            new_appts, skipped = deduplicate(adb.appointments, new_appts)
            if silent < 2:
                print(f"  {skipped} records skipped (duplicate).", file=sys.stderr)
                print(f"  {len(new_appts)} records will be added.", file=sys.stderr)

    # CSV 取り込み
    elif args.input_csv:
        if args.appt is None and args.event is None and args.todo is None:
            parser.error('CSV 取り込みには -a / -e / -t のいずれかを指定してください')
        kind_str = ('event' if include_event else
                    'todo'  if include_todo  else 'appt')
        if silent < 2:
            print(f"Importing {args.input_csv} as {kind_str} ...", file=sys.stderr)
        try:
            new_appts = import_csv(args.input_csv, kind=kind_str,
                                   encoding=csv_enc, alarm=alarm_val)
        except Exception as e:
            print(f"Error importing CSV: {e}", file=sys.stderr)
            sys.exit(1)
        if silent < 2:
            print(f"  {len(new_appts)} records imported.", file=sys.stderr)

        # CSV: dedup は opt-in (--dedup)
        if args.dedup and not args.merge:
            new_appts, skipped = deduplicate(adb.appointments, new_appts)
            if silent < 2:
                print(f"  {skipped} records skipped (duplicate).", file=sys.stderr)
                print(f"  {len(new_appts)} records will be added.", file=sys.stderr)
        elif args.dedup and args.merge:
            if silent < 2:
                print("  Warning: --dedup は -m (新規作成モード) では無視されます。",
                      file=sys.stderr)

    # ADB書き出し
    if args.output_adb:
        if silent < 2:
            print(f"Writing {args.output_adb} ...", file=sys.stderr)
        try:
            adb.write(args.output_adb, merge=args.merge,
                      new_appointments=new_appts if new_appts else None)
        except Exception as e:
            print(f"Error writing ADB: {e}", file=sys.stderr)
            sys.exit(1)
        if silent < 2:
            print("Done.", file=sys.stderr)

    # CSV/TSV 出力
    if args.output_adb is None or (include_appt or include_event or include_todo):
        if include_appt or include_event or include_todo:
            if csv_enc == 'cp932':
                out_stream = io.TextIOWrapper(
                    sys.stdout.buffer, encoding='cp932', errors='replace')
            else:
                out_stream = sys.stdout

            export_csv(
                adb.appointments,
                include_appt=include_appt,
                include_event=include_event,
                include_todo=include_todo,
                note_mode=note_mode,
                date_start=date_start,
                date_end=date_end,
                output=out_stream,
                encoding=csv_enc,
                tab_mode=args.tab_mode,
            )


if __name__ == '__main__':
    main()
