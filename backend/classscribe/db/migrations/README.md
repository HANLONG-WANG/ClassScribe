# Database migrations

Run migrations with an explicit local database URL:

```bash
CLASSSCRIBE_DATABASE_URL=sqlite:////absolute/path/classscribe.sqlite3 uv run alembic upgrade head
```

The default URL targets `/tmp` only to make accidental development invocations non-destructive.
Production startup derives the authoritative path from `AppPaths.data` and supplies it explicitly.
