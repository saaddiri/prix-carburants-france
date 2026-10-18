"""Connexion à PostgreSQL à partir du fichier .env (jamais de mot de passe dans le code)."""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()  # lit .env à la racine du projet


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB", "fuel"),
        user=os.getenv("POSTGRES_USER", "fuel"),
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    )