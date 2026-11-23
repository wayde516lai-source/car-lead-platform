"""Excel demand preflight and atomic import into the shared demand pool.

Uploads never fetch remote data. Link-source configuration and demand reconciliation
are reused; files create independent, unscheduled sources and never remove other rows.
"""
import io
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from domain import Fault, SH, later, now, uid
from security import canonical, digest
from synced_demands import source_time, split_values

REQUIRED = ('品牌', '车型', '城市&地区', '下单量')
ALIASES = {'品牌名称': '品牌', '车系': '车型', '车系名称': '车型', '城市': '城市&地区',
           '下单时间': '下单日期', '需求量': '下单量', '数量': '下单量', '备注': '其他信息备注（渠道编辑）'}


def read_workbook(filename, content):
    """Read the first sheet, rejecting formulas and oversized/invalid workbooks."""
    if Path(filename).suffix.lower() != '.xlsx':
        raise Fault('FILE_TYPE', '需求导入仅支持.xlsx，请用Excel或WPS另存为.xlsx')
    if not content or len(content) > 10 * 1024 * 1024:
        raise Fault('FILE_TOO_LARGE', '文件为空或超过10MB')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise Fault('FILE_TOO_LARGE', '表格解压后超过50MB')
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        try:
            sheet = workbook.worksheets[0]
            sheet.reset_dimensions()
            rows = []
            for cells in sheet.iter_rows():
                if len(cells) > 100 or len(rows) > 5000:
                    raise Fault('TOO_MANY_ROWS', '最多5000条需求、100列')
                if any(cell.data_type == 'f' for cell in cells):
                    raise Fault('EXCEL_FORMULA', '需求表不能包含公式，请粘贴为值后上传')
                rows.append([cell.value for cell in cells])
        finally:
            workbook.close()
    except Fault:
        raise
    except Exception:
        raise Fault('FILE_INVALID', 'Excel文件不可读取，请另存为未加密的.xlsx') from None
    if len(rows) < 2:
        raise Fault('FILE_EMPTY', '表格需要表头和至少一条需求')
    return rows


def cell_text(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


class DemandExcel:
    def __init__(self, platform, synchronizer):
        self.platform = platform
        self.sync = synchronizer

    def preview(self, actor, filename, content, config):
        """Persist a two-hour, actor-scoped preview without creating any demands."""
        self.platform.require(actor, 'demand')
        if not isinstance(config, dict) or not isinstance(filename, str):
            raise Fault('IMPORT_INVALID', '导入配置或文件名无效')
        uploaded_at = now()
        defaults = datetime.fromisoformat(uploaded_at).astimezone(SH).isoformat(timespec='seconds')
        config = {key: config.get(key) for key in ('partner_id', 'media_id', 'sale_project', 'name',
                                                  'route_id', 'quantity_basis') if config.get(key)}
        config.update(enabled=0, interval_minutes=30)
        conn = self.platform.connect()
        try:
            self.sync.validate_config(conn, actor, config)
            existing = {row[0] for row in conn.execute(
                'SELECT demand_no FROM demands WHERE org_id=? AND media_id=?',
                (actor['org_id'], config['media_id']),
            )}
        finally:
            conn.close()
        rows = read_workbook(filename, content)
        headers = [ALIASES.get(cell_text(value), cell_text(value)) for value in rows[0]]
        named = [name for name in headers if name]
        if len(set(named)) != len(named):
            raise Fault('EXCEL_HEADERS', '表头字段重复（含城市/城市&地区等别名）')
        missing = [name for name in REQUIRED if name not in named]
        if missing:
            raise Fault('EXCEL_HEADERS', '缺少必填列：' + '、'.join(missing))
        records, errors, seen = [], [], set()
        for row_number, row in enumerate(rows[1:], 2):
            if not any(cell_text(value) for value in row):
                continue
            values = {name: cell_text(row[index]) if index < len(row) else ''
                      for index, name in enumerate(headers) if name}
            raw = dict(values)
            for field in ('车型', '城市&地区'):
                values[field] = '、'.join(split_values(values.get(field, '')))
            reasons = [name + '不能为空' for name in REQUIRED if not values.get(name)]
            external_no = values.get('需求编号', '')
            values['需求编号'] = external_no
            # Source rows require a stable key even when no external number exists.
            # This internal key must never be presented or sent as a media number.
            key = external_no or uid('excel_row_')
            if external_no and external_no in seen:
                reasons.append('文件内需求编号重复')
            if external_no:
                seen.add(external_no)
            if external_no and external_no in existing:
                reasons.append('该媒体需求编号已存在，请勿重复导入或覆盖链接需求')
            if not re.fullmatch(r'[1-9][0-9]{0,6}', values.get('下单量', '')):
                reasons.append('下单量须为正整数，最多7位')
            # Defaults use receipt time, not commit time; even a delayed confirmation
            # must preserve the upload instant shown in the preview.
            for name in ('下单日期', '开始时间'):
                values[name] = values.get(name) or defaults
            values['结束时间'] = values.get('结束时间') or '待定'
            try:
                start = source_time(values['开始时间'])
                order = source_time(values['下单日期'])
                end = source_time(values.get('结束时间（渠道编辑）') or values['结束时间'], True)
                if not start or not order:
                    raise ValueError('下单日期和开始时间不能为待定')
                if end and end <= start:
                    raise ValueError('结束时间须晚于开始时间')
            except ValueError as error:
                reasons.append(str(error))
            records.append({'key': key, 'row_number': row_number, 'values': values, 'raw_values': raw})
            if reasons:
                errors.append({'row_number': row_number, 'demand_no': external_no, 'errors': reasons})
        if not records:
            raise Fault('FILE_EMPTY', '没有可导入的需求行')
        batch_id = uid('dei_')
        payload = {'config': config, 'records': records, 'errors': errors,
                   'headers': list(dict.fromkeys(['需求编号'] + named + ['下单日期', '开始时间', '结束时间']))}
        with self.platform.tx() as conn:
            conn.execute('''INSERT INTO demand_excel_batches
                (id,org_id,actor_id,filename,uploaded_at,expires_at,payload_json)
                VALUES(?,?,?,?,?,?,?)''',
                         (batch_id, actor['org_id'], actor['id'], Path(filename).name[:200],
                          uploaded_at, later(hours=2), canonical(payload)))
            self.platform.audit(conn, actor, 'DEMAND_EXCEL_PREVIEW', batch_id,
                                {'rows': len(records), 'errors': len(errors)})
        return {'id': batch_id, 'uploaded_at': uploaded_at, 'total': len(records),
                'valid': len(records) - len(errors), 'errors': errors,
                'preview': records[:20], 'can_commit': not errors}

    def commit(self, actor, batch_id):
        """Atomically create all previewed demands; retries return the same result.

        A conflict or mapping error rolls back the whole batch. No network is used.
        """
        self.platform.require(actor, 'demand')
        with self.platform.tx() as conn:
            batch = self.platform.one(conn, 'demand_excel_batches', batch_id, actor)
            if batch['actor_id'] != actor['id']:
                raise Fault('FORBIDDEN', '只能提交本人预检的需求', 403)
            if batch['result_json']:
                return json.loads(batch['result_json'])
            if batch['expires_at'] <= now():
                raise Fault('IMPORT_EXPIRED', '预检已过期，请重新上传')
            payload = json.loads(batch['payload_json'])
            if payload['errors']:
                raise Fault('IMPORT_ERRORS', '请修正全部错误后重新预检')
            config = payload['config']
            source = self.sync.save(actor, config, _connection=conn, _file_id=batch_id)
            for record in payload['records']:
                if record['values'].get('需求编号') and conn.execute('SELECT 1 FROM demands WHERE org_id=? AND media_id=? AND demand_no=?',
                                (actor['org_id'], config['media_id'], record['key'])).fetchone():
                    raise Fault('DEMAND_DUPLICATE', '需求编号已被创建，请重新预检', 409)
                content_hash = digest(canonical({'values': record['values'], 'raw': record['raw_values']}))
                conn.execute('''INSERT INTO demand_sync_projects
                    (id,source_id,org_id,source_key,row_number,values_json,raw_json,content_hash,
                     is_present,first_seen_at,last_seen_at,changed_at)
                    VALUES(?,?,?,?,?,?,?,?,1,?,?,?)''',
                             (uid('dp_'), source['id'], actor['org_id'], record['key'],
                              record['row_number'], canonical(record['values']),
                              canonical(record['raw_values']), content_hash,
                              batch['uploaded_at'], batch['uploaded_at'], batch['uploaded_at']))
            mapped = self.platform._reconcile_source(conn, actor, source['id'])
            if mapped['errors']:
                raise Fault('DEMAND_MAPPING_FAILED', '需求无法关联交付路由或字段，请检查配置后重新预检')
            stamp = now()
            conn.execute('UPDATE demand_sync_sources SET title=?,headers=?,last_success_at=? WHERE id=?',
                         (batch['filename'], canonical(payload['headers']), stamp, source['id']))
            result = {'source_id': source['id'], 'created': mapped['linked'], 'uploaded_at': batch['uploaded_at']}
            conn.execute('UPDATE demand_excel_batches SET source_id=?,result_json=? WHERE id=?',
                         (source['id'], canonical(result), batch_id))
            self.platform.audit(conn, actor, 'DEMAND_EXCEL_COMMIT', batch_id, result)
            return result


def template_bytes():
    """Generate the application's blank XLSX template; no business data included."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '需求导入'
    headers = ['需求编号', *REQUIRED, '下单日期', '开始时间', '结束时间', '类型', '其他信息备注（渠道编辑）']
    sheet.append(headers)
    sheet.freeze_panes = 'A2'
    for index, cell in enumerate(sheet[1], 1):
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='164D3E')
        sheet.column_dimensions[get_column_letter(index)].width = 24
    sheet['A2'].number_format = '@'
    sheet.auto_filter.ref = 'A1:J1'
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
