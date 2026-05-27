SELECT
{select_fields}
FROM campaign
WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
ORDER BY segments.month DESC, metrics.cost_micros DESC
