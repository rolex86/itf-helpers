SELECT
{select_fields}
FROM ad_group_ad
WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
ORDER BY metrics.cost_micros DESC
