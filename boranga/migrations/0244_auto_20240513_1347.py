# Generated by Django 3.2.25 on 2024-05-13 05:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("boranga", "0243_merge_0242_auto_20240508_1348_0242_auto_20240508_1431"),
    ]

    operations = [
        migrations.CreateModel(
            name="Profile",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("email_user", models.IntegerField()),
                (
                    "organisation",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("position", models.CharField(blank=True, max_length=100, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="UserCategory",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
            ],
        ),
    ]