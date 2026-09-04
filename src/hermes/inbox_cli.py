"""CLI inbox viewer."""
import click
from .config import settings
from .tasks import TaskStore


@click.command()
@click.option("--limit", default=10)
def main(limit: int):
    rows = TaskStore(settings.hermes_db_path).list_tasks(limit)
    for r in rows:
        click.echo(f"{r['id']} [{r['status']}] ({r['project']}/{r['strategy']}) {r['text'][:80]}")


if __name__ == "__main__":
    main()
