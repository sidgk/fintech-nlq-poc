{#
  Use the model's +schema value verbatim (silver / gold) instead of dbt's
  default "<target>_<custom>" prefixing. Keeps the Medallion schemas clean.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
