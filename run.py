import os

from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    if os.environ.get("MAILPILOT_ENV", "development").strip().casefold() == "production":
        raise RuntimeError("Do not run the Flask development server in production; use the documented WSGI server command.")

from mailpilot import create_app

app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
