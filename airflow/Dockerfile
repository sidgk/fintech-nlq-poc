FROM apache/airflow:2.10.5-python3.11

# Cosmos lives in the Airflow environment (it's an Airflow provider).
RUN pip install --no-cache-dir "astronomer-cosmos==1.7.*"

# dbt goes in an ISOLATED virtualenv. Airflow and dbt pin conflicting versions
# of common libs (pydantic, jinja, etc.); keeping dbt separate is the pattern
# Astronomer recommends. Cosmos invokes dbt via this venv's executable.
# /opt is root-owned, so create the venv as root, then drop back to airflow.
USER root
RUN python -m venv /opt/dbt_venv && \
    /opt/dbt_venv/bin/pip install --no-cache-dir "dbt-postgres==1.9.*"
USER airflow
