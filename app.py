"""Run the local RHIT dashboard with: python app.py."""

from rhit import create_app
from rhit.reception import run_local

app = create_app()

if __name__ == "__main__":
    run_local(app)
