"""Fakes de conexion PostgreSQL para los tests unitarios de H6.

Los servicios transaccionales de H6 (`ApprovalService`, `GovernedResumeService`,
`ExecutionOrchestrator`, `ApprovalResolver`) aceptan un `connection_factory`
inyectable. Estos fakes implementan el subconjunto del contrato psycopg que
usan esos servicios (autocommit, cursor(), execute(), fetchone(), commit(),
rollback(), close()) para probar sin una base de datos real.

El `route(sql, params)` retorna `(rowcount, row)`: cantidad de filas afectadas
y la fila que `fetchone()` debe devolver (None mas comunmente).
"""


class FakeCursor:
    """Cursor falsa que delega el comportamiento a `conn.route`."""

    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.record.append((sql, params))
        rowcount, row = self.conn.route(sql, params)
        self.rowcount = rowcount
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Conexion falsa basada en un `route(sql, params) -> (rowcount, row)`."""

    def __init__(self, route):
        self.autocommit = True
        self.route = route
        self.record = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True