"""单次只读请求的重复SELECT合并；不跨请求缓存，也不用于写入或发送校验。"""


class ReadResult:
    def __init__(self, rows):
        self.rows = rows
        self.position = 0

    def fetchone(self):
        if self.position >= len(self.rows):
            return None
        row = self.rows[self.position]
        self.position += 1
        return row

    def fetchall(self):
        rows = list(self.rows[self.position:])
        self.position = len(self.rows)
        return rows

    def __iter__(self):
        while self.position < len(self.rows):
            yield self.fetchone()


class ReadQueries:
    """只读页面请求共享已读SELECT结果，每次请求新建；限制缓存条数及行数。"""
    def __init__(self, connection):
        self.connection = connection
        self.cache = {}
        self.cached_rows = 0

    def execute(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith('SELECT'):
            raise ValueError('只读请求不能执行写入语句')
        key = (sql, tuple(parameters))
        if key in self.cache:
            return ReadResult(self.cache[key])
        rows = tuple(self.connection.execute(sql, parameters).fetchall())
        if len(self.cache) < 4096 and self.cached_rows + len(rows) <= 30000:
            self.cache[key] = rows
            self.cached_rows += len(rows)
        return ReadResult(rows)

    def close(self):
        self.cache.clear()
        self.connection.close()
