import pg from 'pg';
import {writeFile,mkdir} from 'node:fs/promises';
const pool=new pg.Pool({connectionString:'postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'});
try{
 const columns=(await pool.query("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='properties' ORDER BY ordinal_position")).rows;
 const fields=['price','currency','bedrooms','bathrooms','floor_area_sqft','land_area_sqft','build_year','district_code','region_code','postal_code','tenure_category','property_type','latitude','longitude','mrt_distance_m','mrt_walking_mins'];
 const rows=(await pool.query('SELECT '+fields.join(',')+" FROM properties WHERE listing_type='RENT'")).rows;
 const coverage=fields.map(field=>({field,rows:rows.length,present:rows.filter(r=>r[field]!==null&&r[field]!==undefined&&String(r[field]).trim()!=='').length,zero_values:rows.filter(r=>r[field]!==null&&Number(r[field])===0).length}));
 const raw=(await pool.query("SELECT count(*) FILTER (WHERE raw_json IS NOT NULL) AS populated_raw_json FROM properties WHERE listing_type='RENT'")).rows[0];
 const enums=await Promise.all(['property_type','region_code','tenure_category'].map(async field=>({field,values:(await pool.query(`SELECT ${field} AS value,count(*) AS count FROM properties WHERE listing_type='RENT' GROUP BY ${field} ORDER BY ${field}`)).rows})));
 const absent=['amenities','private_bathroom','description','furnishing','rental_scope','lease_months','move_in_date','commute_time','current_availability'].filter(f=>!columns.some(c=>c.column_name===f));
 const report={checked_at:new Date().toISOString(),database:'propertyguru_sample',scope:'475 historical rental listings; aggregate metadata only, no contacts or raw records',rental_rows:rows.length,columns,coverage,absent_business_columns:absent,populated_raw_json:Number(raw.populated_raw_json),enums,interpretation:['bathrooms is a count, not a private-use-right or room/whole-unit label','0 bedrooms is preserved as a source count; do not infer studio or rental scope','empty regional, tenure and MRT fields are unknown; lack of a match does not prove failure','No private-bathroom evidence can be recovered from this snapshot: no column, description or populated raw JSON']};
 await mkdir('evaluation/reports',{recursive:true});await writeFile('evaluation/reports/rental-data-audit.json',JSON.stringify(report,null,2));console.log(JSON.stringify({rental_rows:rows.length,absent_business_columns:absent,populated_raw_json:report.populated_raw_json,coverage},null,2));
}finally{await pool.end();}
