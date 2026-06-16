SELECT
{select_fields}
FROM shopping_performance_view
WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
ORDER BY metrics.cost_micros DESC
