from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Full reembed of MEMORY.md into the vector search index."

    def handle(self, *args, **options) -> None:
        from agent.memory.long_term import full_reembed

        self.stdout.write("Running full reembed of MEMORY.md…")
        full_reembed()
        self.stdout.write(self.style.SUCCESS("Done."))
