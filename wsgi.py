"""WSGI entry point for a production server such as Waitress."""

from mailpilot import create_app


app = create_app()
