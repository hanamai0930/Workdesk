"""Small DB-API compatibility surface backed by the Go owner's transaction."""
import sqlite3

from . import call


class Row:
    def __init__(self, columns, values):
        self._columns, self._values = tuple(columns), tuple(values)

    def keys(self):
        return list(self._columns)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = next(index for index, name in enumerate(self._columns) if name.casefold() == key.casefold())
            except StopIteration:
                raise IndexError('No item with that key') from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class Cursor:
    def __init__(self, connection, result=None):
        self.connection = connection
        self.row_factory = connection.row_factory
        self.arraysize = 1
        self._load(result or {})

    def _load(self, result):
        self.columns = result.get('columns') or []
        self.description = tuple((name, None, None, None, None, None, None) for name in self.columns) or None
        self.rows = result.get('rows') or []
        self.offset = 0
        self.lastrowid = result.get('lastrowid')
        self.rowcount = result.get('rowcount', -1)

    def execute(self, sql, parameters=()):
        self._load(self.connection._execute(sql, parameters))
        return self

    def executemany(self, sql, parameters):
        count = 0
        for values in parameters:
            self.execute(sql, values)
            count += max(0, self.rowcount)
        self.rowcount = count
        return self

    def fetchone(self):
        if self.offset >= len(self.rows):
            return None
        value = self.rows[self.offset]
        self.offset += 1
        if isinstance(value, dict):
            value = [value[name] for name in self.columns]
        return Row(self.columns, value) if self.row_factory is not None else tuple(value)

    def fetchmany(self, size=None):
        result = []
        for unused in range(self.arraysize if size is None else size):
            value = self.fetchone()
            if value is None:
                break
            result.append(value)
        return result

    def fetchall(self):
        return self.fetchmany(len(self.rows) - self.offset)

    def __iter__(self):
        return self

    def __next__(self):
        value = self.fetchone()
        if value is None:
            raise StopIteration
        return value

    def close(self):
        self.offset = len(self.rows)


class Connection:
    def __init__(self, write=False):
        self.session = call('db.begin', {'write': bool(write)})['session']
        self.in_transaction = True
        self._closed = False
        self.row_factory = sqlite3.Row
        self.total_changes = 0

    def _execute(self, sql, parameters=()):
        if self._closed:
            raise sqlite3.ProgrammingError('核心事务已经结束。')
        result = call('db.execute', {'session': self.session, 'sql': sql, 'args': list(parameters)})
        self.total_changes = result.get('total_changes', self.total_changes + max(0, result.get('rowcount', 0)))
        self.in_transaction = result.get('in_transaction', self.in_transaction)
        return result

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def cursor(self):
        return Cursor(self)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def commit(self):
        if not self._closed:
            try:
                call('db.commit', {'session': self.session})
            finally:
                self._closed = True
                self.in_transaction = False

    def rollback(self):
        if not self._closed:
            try:
                call('db.rollback', {'session': self.session})
            finally:
                self.in_transaction = False
                self._closed = True

    def close(self):
        self.rollback()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.rollback() if kind is not None else self.commit()


def trusted_connection(value):
    return type(value) in (sqlite3.Connection, Connection)
