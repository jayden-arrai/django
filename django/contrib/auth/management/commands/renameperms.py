from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction


class Command(BaseCommand):
    help = (
        "Renames built-in permission codenames and labels to match "
        "AUTH_PERMISSIONS_MAP. Use this once to migrate an existing "
        "site after setting AUTH_PERMISSIONS_MAP."
    )
    requires_migrations_checks = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default=DEFAULT_DB_ALIAS,
            choices=tuple(connections),
            help='Specifies the database to update. Defaults to "default".',
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Display what would be renamed without making any changes.",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from django.contrib.auth.models import Permission

        codename_map = settings.AUTH_PERMISSIONS_MAP
        if not codename_map:
            self.stdout.write(
                self.style.WARNING(
                    "AUTH_PERMISSIONS_MAP is empty. Nothing to rename."
                )
            )
            return

        database = options["database"]
        dry_run = options["dry_run"]
        verbosity = options["verbosity"]

        if not router.allow_migrate_model(database, Permission):
            return

        planned = []
        conflicts = []

        for app_config in apps.get_app_configs():
            if not app_config.models_module:
                continue
            for model in app_config.get_models():
                opts = model._meta
                content_type = ContentType.objects.db_manager(database).get_for_model(
                    model, for_concrete_model=False
                )

                for action in opts.default_permissions:
                    if action not in codename_map:
                        continue

                    new_action = codename_map[action]
                    old_codename = f"{action}_{opts.model_name}"
                    new_codename = f"{new_action}_{opts.model_name}"

                    if old_codename == new_codename:
                        continue

                    try:
                        perm = Permission.objects.using(database).get(
                            content_type=content_type,
                            codename=old_codename,
                        )
                    except Permission.DoesNotExist:
                        # Already renamed or was never created.
                        continue

                    if Permission.objects.using(database).filter(
                        content_type=content_type,
                        codename=new_codename,
                    ).exists():
                        conflicts.append(
                            (app_config.label, old_codename, new_codename)
                        )
                        continue

                    new_name = f"Can {new_action} {opts.verbose_name_raw}"
                    planned.append((perm, old_codename, new_codename, new_name))

        for app_label, old, new in conflicts:
            self.stderr.write(
                self.style.ERROR(
                    f"{app_label}: cannot rename '{old}' to '{new}': "
                    f"a permission with codename '{new}' already exists."
                )
            )

        if planned:
            if dry_run:
                for _, old_codename, new_codename, new_name in planned:
                    self.stdout.write(
                        f"Would rename: {old_codename} -> {new_codename} ('{new_name}')"
                    )
            else:
                with transaction.atomic(using=database):
                    for perm, _, new_codename, new_name in planned:
                        perm.codename = new_codename
                        perm.name = new_name
                        perm.save(update_fields={"codename", "name"}, using=database)
                if verbosity >= 1:
                    for _, old_codename, new_codename, new_name in planned:
                        self.stdout.write(
                            f"Renamed: {old_codename} -> {new_codename} ('{new_name}')"
                        )

        if verbosity >= 1:
            verb = "Would rename" if dry_run else "Renamed"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{verb} {len(planned)} permission(s), "
                    f"{len(conflicts)} conflict(s) skipped."
                )
            )
