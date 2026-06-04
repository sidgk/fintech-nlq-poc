"""
dbt_medallion — orchestrates the Bronze → Silver → Gold dbt pipeline as an
Airflow DAG, using Astronomer Cosmos (dbt Core, NOT dbt Cloud).

Cosmos parses the dbt project and renders EACH model + test as its own Airflow
task, so the manager sees the real lineage in the Airflow Graph view:

    raw.* (bronze, a dbt *source* — ingested, not built here)
        └─▶ stg_* (silver views)  ─▶  fct_payments / dim_* (gold tables)  ─▶  tests

Trigger it from the UI (http://localhost:8080) and watch it run top to bottom.
In production this same DAG runs on a schedule; the dbt project is unchanged.
"""

from datetime import datetime

from cosmos import DbtDag, ProjectConfig, ProfileConfig, ExecutionConfig, RenderConfig
from cosmos.constants import LoadMode

# Paths INSIDE the Airflow container (see docker-compose volumes).
DBT_PROJECT_PATH = "/opt/airflow/dbt"
DBT_EXECUTABLE = "/opt/dbt_venv/bin/dbt"

# Reuse the project's profiles.yml. Inside the container DBT_HOST=db / DBT_PORT=5432
# (the Postgres service on the shared Docker network) are set via compose env.
profile_config = ProfileConfig(
    profile_name="fintech",
    target_name="dev",
    profiles_yml_filepath=f"{DBT_PROJECT_PATH}/profiles.yml",
)

dbt_medallion = DbtDag(
    project_config=ProjectConfig(DBT_PROJECT_PATH),
    profile_config=profile_config,
    execution_config=ExecutionConfig(dbt_executable_path=DBT_EXECUTABLE),
    # DBT_LS: Cosmos runs `dbt ls` at parse time to discover models/tests.
    render_config=RenderConfig(
        load_method=LoadMode.DBT_LS,
        dbt_executable_path=DBT_EXECUTABLE,
    ),
    operator_args={"install_deps": False},  # no dbt packages to install
    # DAG-level args
    dag_id="dbt_medallion",
    schedule=None,                 # manual trigger for the demo
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dbt", "medallion", "fintech"],
)
