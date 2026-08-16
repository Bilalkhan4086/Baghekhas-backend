import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/bagh_test")
os.environ.setdefault("JWT_SECRET", "unit-test-secret-that-is-at-least-32-characters")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault("AWS_STORAGE_BUCKET_NAME", "test-bucket")
os.environ.setdefault("AWS_S3_URL", "https://test-bucket.s3.example.com")
os.environ.setdefault("S3DIRECT_REGION", "us-east-1")
