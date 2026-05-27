SELECT
{select_fields}
FROM ad_group_ad_asset_view
WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
ORDER BY metrics.impressions DESC
