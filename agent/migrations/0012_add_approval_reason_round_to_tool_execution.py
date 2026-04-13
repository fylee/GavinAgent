from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0011_add_parallel_group_to_tool_execution'),
    ]

    operations = [
        migrations.AddField(
            model_name='toolexecution',
            name='round',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='toolexecution',
            name='approval_reason',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
