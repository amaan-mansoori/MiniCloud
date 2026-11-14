import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["APP_ENV"] = "test"
os.environ["SERVICE_ROLE"] = "api"
os.environ["JWT_SECRET_KEY"] = "test-only-minicloud-signing-key-32chars"
os.environ["MIGRATION_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["AUTH_RATE_LIMIT_MAX_REQUESTS"] = "5"
os.environ["AUTH_RATE_LIMIT_WINDOW_SECONDS"] = "60"
