import pandas as pd
import numpy as np
import geopandas as gpd
import h3
import ast, os, time, gc, joblib, json
from math import radians, cos, sin, asin, sqrt
from scipy.spatial import cKDTree
from shapely.geometry import Point
from sklearn.cluster import DBSCAN
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb
import lightgbm as lgb
import warnings
warnings.filterwarnings('ignore')

OUTPUT = '/home/aaryan/flipkart-gridlock/outputs'
CSV = '/home/aaryan/flipkart-gridlock/jan to may police violation_anonymized791b166.csv'
os.makedirs(OUTPUT, exist_ok=True)
T0 = time.time()

def pr(msg):
    print(f'[{time.time()-T0:6.0f}s] {msg}', flush=True)

# ==============================
# HELPERS
# ==============================
def ep(v):
    if pd.isna(v): return 'UNKNOWN'
    try:
        x = ast.literal_eval(v)
        return x[0] if isinstance(x, list) and len(x) > 0 else str(x)
    except: return str(v).strip('[]\" ')

def cv(v):
    if pd.isna(v): return 1
    try:
        x = ast.literal_eval(v)
        return len(x) if isinstance(x, list) else 1
    except: return 1

VW = {'SCOOTER':0.4,'MOTOR CYCLE':0.3,'MOPED':0.3,'CAR':0.7,'MAXI-CAB':0.8,
      'PASSENGER AUTO':0.9,'VAN':0.8,'LGV':1.0,'GOODS AUTO':0.9,'PRIVATE BUS':1.0,'TANKER':1.0}
VS = {'WRONG PARKING':0.6,'NO PARKING':0.7,'PARKING IN A MAIN ROAD':0.9,
      'PARKING ON FOOTPATH':0.85,'PARKING NEAR ROAD CROSSING':0.95,
      'PARKING NEAR BUSTOP/SCHOOL/HOSPITAL ETC':0.95,'DOUBLE PARKING':0.9,
      'PARKING OPPOSITE TO ANOTHER PARKED VEHICLE':0.8,'DEFECTIVE NUMBER PLATE':0.3,'UNKNOWN':0.5}
TM = {'Morning_Peak':1.4,'Evening_Peak':1.5,'Midday':1.0,'Night':0.6,'Late_Night':0.4}
def tod(h):
    if 6<=h<10: return 'Morning_Peak'
    elif 10<=h<17: return 'Midday'
    elif 17<=h<21: return 'Evening_Peak'
    elif 21<=h<24: return 'Night'
    else: return 'Late_Night'

# ==============================
# STEP 1: LOAD & CLEAN
# ==============================
pr('Step 1: Loading data...')
use_cols = ['id','latitude','longitude','violation_type','vehicle_type',
            'police_station','junction_name','created_datetime','closed_datetime',
            'vehicle_number','validation_status']
df = pd.read_csv(CSV, usecols=use_cols)
df = df.drop_duplicates()
df['created_datetime'] = pd.to_datetime(df['created_datetime'], errors='coerce', utc=True)
df['closed_datetime'] = pd.to_datetime(df['closed_datetime'], errors='coerce', utc=True)
df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
df = df.dropna(subset=['latitude','longitude','created_datetime'])
df = df[(df['latitude']>12.5)&(df['latitude']<13.5)]
df = df[(df['longitude']>77.0)&(df['longitude']<78.0)]
df['vp'] = df['violation_type'].apply(ep)
df['vc'] = df['violation_type'].apply(cv)
df['im'] = df['vc'] > 1
df['hour'] = df['created_datetime'].dt.hour
df['dow'] = df['created_datetime'].dt.dayofweek
df['month'] = df['created_datetime'].dt.month
df['tod'] = df['hour'].apply(tod)
df['vw'] = df['vehicle_type'].map(VW).fillna(0.5)
df['sev'] = df.apply(lambda r: min(VS.get(r['vp'],0.5)*r['vw']*TM.get(r['tod'],1.0)*(1.3 if r['im'] else 1.0), 2.0), axis=1)
df['res_hrs'] = (df['closed_datetime']-df['created_datetime']).dt.total_seconds()/3600
df = df.drop(columns=['violation_type','closed_datetime'])
gc.collect()
pr(f'Loaded & cleaned: {len(df):,} rows')

# ==============================
# STEP 2: CRS CONVERSION (EPSG:32643)
# ==============================
pr('Step 2: CRS Conversion...')
geometry = [Point(lon, lat) for lat, lon in zip(df['latitude'], df['longitude'])]
gdf = gpd.GeoDataFrame(df[['id']].copy(), geometry=geometry, crs='EPSG:4326')
gdf_utm = gdf.to_crs(epsg=32643)
df['x_meters'] = gdf_utm.geometry.x
df['y_meters'] = gdf_utm.geometry.y
d = sqrt((df.iloc[1]['x_meters']-df.iloc[0]['x_meters'])**2 + (df.iloc[1]['y_meters']-df.iloc[0]['y_meters'])**2)
pr(f'CRS OK: first 2 points = {d:.1f}m apart')
del gdf, gdf_utm, geometry; gc.collect()

# ==============================
# STEP 3: ROAD SNAPPING (OSMnx)
# ==============================
pr('Step 3: Road Snapping (OSMnx)...')
try:
    import osmnx as ox
    G = ox.graph_from_point((12.9716, 77.5946), dist=15000, network_type='drive')
    pr(f'Road network: {len(G.nodes)} nodes, {len(G.edges)} edges')

    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    edges_utm = edges.to_crs(epsg=32643)

    edge_points = []
    edge_data = []
    for idx, row in edges_utm.iterrows():
        geom = row.geometry
        if geom is not None:
            if geom.geom_type == 'LineString':
                mid = geom.interpolate(0.5, normalized=True)
                edge_points.append((mid.x, mid.y))
                edge_data.append({'edge_id': str(idx), 'length': row.get('length', 0), 'name': row.get('name', 'unknown'), 'highway': row.get('highway', 'unknown')})
            elif geom.geom_type == 'MultiLineString':
                for part in geom.geoms:
                    mid = part.interpolate(0.5, normalized=True)
                    edge_points.append((mid.x, mid.y))
                    edge_data.append({'edge_id': str(idx), 'length': row.get('length', 0), 'name': row.get('name', 'unknown'), 'highway': row.get('highway', 'unknown')})

    edge_points = np.array(edge_points)
    edge_df = pd.DataFrame(edge_data)

    tree = cKDTree(edge_points)
    violation_coords = df[['x_meters', 'y_meters']].values
    distances, indices = tree.query(violation_coords, k=1)

    df['snap_distance_m'] = distances
    df['road_name'] = edge_df.iloc[indices]['name'].values
    df['road_highway'] = edge_df.iloc[indices]['highway'].values
    df['road_length_m'] = edge_df.iloc[indices]['length'].values
    df['road_edge_id'] = edge_df.iloc[indices]['edge_id'].values

    df['road_highway'] = df['road_highway'].apply(lambda x: x[0] if isinstance(x, list) else x)
    df['road_name'] = df['road_name'].apply(lambda x: x[0] if isinstance(x, list) else x)
    snapped = df[df['snap_distance_m'] <= 100]
    pr(f'Snapped {len(snapped):,}/{len(df):,} ({len(snapped)/len(df)*100:.1f}%) within 100m of road')
    pr(f'Unique roads: {df["road_name"].nunique()}')
except Exception as e:
    pr(f'OSMnx failed: {e}, using defaults')
    df['snap_distance_m'] = 0
    df['road_name'] = 'unknown'
    df['road_highway'] = 'unknown'
    df['road_length_m'] = 100
    df['road_edge_id'] = -1
gc.collect()

# ==============================
# STEP 4: H3 INDEXING
# ==============================
pr('Step 4: H3 Indexing...')
df['h3_index'] = df.apply(lambda r: h3.latlng_to_cell(r['latitude'], r['longitude'], 8), axis=1)
df['h3_lat'] = df['h3_index'].apply(lambda x: h3.cell_to_latlng(x)[0])
df['h3_lon'] = df['h3_index'].apply(lambda x: h3.cell_to_latlng(x)[1])
n_hexes = df['h3_index'].nunique()
pr(f'H3 Res 8: {n_hexes} hexagons')

def get_hex_boundary(h3_idx):
    return [(lat, lon) for lat, lon in h3.cell_to_boundary(h3_idx)]

def most_common(s):
    vc = s.dropna().value_counts()
    return vc.index[0] if len(vc) > 0 else 'unknown'

h3_stats = df.groupby('h3_index').agg(
    center_lat=('latitude','mean'), center_lon=('longitude','mean'),
    h3_lat=('h3_lat','first'), h3_lon=('h3_lon','first'),
    violation_count=('id','count'), unique_vehicles=('vehicle_number','nunique'),
    avg_severity=('sev','mean'), avg_vehicle_weight=('vw','mean'),
    multi_violation_pct=('im','mean'),
    top_violation=('vp', lambda x: most_common(x)),
    top_vehicle=('vehicle_type', lambda x: most_common(x)),
    police_station=('police_station', lambda x: most_common(x)),
    peak_hour=('hour', lambda x: x.value_counts().index[0]),
    primary_road=('road_name', lambda x: most_common(x)),
    highway_type=('road_highway', lambda x: most_common(x)),
).reset_index()
h3_stats['violations_per_day'] = h3_stats['violation_count'] / 150
h3_stats['boundary'] = h3_stats['h3_index'].apply(get_hex_boundary)
pr(f'H3 aggregation: {len(h3_stats)} zones')

# ==============================
# STEP 5: VELOCITY DEFICIT (ΔV)
# ==============================
pr('Step 5: Velocity Deficit...')
SPEED_LIMITS = {'primary':50,'secondary':45,'tertiary':40,'residential':30,'unclassified':35,'unknown':35}
df['v_free_kmh'] = df['road_highway'].map(SPEED_LIMITS).fillna(35)

road_vd = df.groupby('road_edge_id')['id'].count()
road_dn = (road_vd / road_vd.max()).to_dict()
df['road_density_factor'] = df['road_edge_id'].map(road_dn).fillna(0)
df['v_free_adjusted'] = df['v_free_kmh'] * (1 - 0.3 * df['road_density_factor'])

LANE_BLOCKAGE = {'WRONG PARKING':0.15,'NO PARKING':0.10,'PARKING IN A MAIN ROAD':0.30,
    'PARKING ON FOOTPATH':0.05,'DOUBLE PARKING':0.40,'PARKING NEAR ROAD CROSSING':0.25,
    'PARKING NEAR BUSTOP/SCHOOL/HOSPITAL ETC':0.20,'PARKING OPPOSITE TO ANOTHER PARKED VEHICLE':0.35,
    'DEFECTIVE NUMBER PLATE':0.0,'UNKNOWN':0.10}
df['capacity_reduction'] = df['vp'].map(LANE_BLOCKAGE).fillna(0.10)
df['v_current_kmh'] = df['v_free_adjusted'] * (1 - df['capacity_reduction'])
df['delta_v_kmh'] = df['v_free_adjusted'] - df['v_current_kmh']
df['delta_v_pct'] = (df['delta_v_kmh'] / df['v_free_adjusted'].clip(lower=1) * 100).clip(0, 100)
df['time_loss_min_per_km'] = ((1/df['v_current_kmh'].clip(lower=1)) - (1/df['v_free_adjusted'].clip(lower=1))) * 60

h3_velocity = df.groupby('h3_index').agg(
    avg_delta_v=('delta_v_kmh','mean'), avg_delta_v_pct=('delta_v_pct','mean'),
    avg_time_loss=('time_loss_min_per_km','mean')
).reset_index()
h3_stats = h3_stats.merge(h3_velocity, on='h3_index', how='left')
pr(f'Avg ΔV: {df["delta_v_kmh"].mean():.1f} km/h, time loss: {df["time_loss_min_per_km"].mean():.2f} min/km')

# ==============================
# STEP 6: CPI
# ==============================
pr('Step 6: Congestion Penalty Index...')
def compute_cpi(row):
    w1,w2,w3,w4 = 0.30,0.25,0.30,0.15
    d_norm = row.get('violations_per_day',0)
    highway_scores = {'motorway':1.0,'trunk':0.9,'primary':0.85,'secondary':0.75,'tertiary':0.65,'residential':0.4,'unclassified':0.5,'living_street':0.3,'service':0.3,'unknown':0.5}
    s_sign = highway_scores.get(str(row.get('highway_type','unknown')),0.5)
    v_def = row.get('avg_delta_v_pct',0)/100.0
    t_sev = row.get('avg_severity',0)
    return round(w1*min(d_norm/50,1.0) + w2*s_sign + w3*v_def + w4*min(t_sev/1.5,1.0), 4)

h3_stats['cpi'] = h3_stats.apply(compute_cpi, axis=1)
h3_stats['risk_level'] = pd.qcut(h3_stats['cpi'].clip(lower=0.001), q=4, labels=['Low','Medium','High','Critical'], duplicates='drop')
h3_stats['enforcement_priority'] = (
    0.35*(h3_stats['violation_count']/max(h3_stats['violation_count'].max(), 1)) +
    0.25*(h3_stats['violations_per_day']/max(h3_stats['violations_per_day'].max(), 0.01)) +
    0.20*(h3_stats['avg_delta_v']/max(h3_stats['avg_delta_v'].max(), 1)) +
    0.20*h3_stats['multi_violation_pct']
)
pr(f'CPI: {h3_stats["cpi"].min():.3f} - {h3_stats["cpi"].max():.3f}')
pr(f'Risk: {h3_stats["risk_level"].value_counts().to_dict()}')

# ==============================
# STEP 7: PREDICTIVE ENGINE
# ==============================
pr('Step 7: Predictive Engine (XGBoost + LightGBM)...')
df['hour_sin'] = np.sin(2*np.pi*df['hour']/24)
df['hour_cos'] = np.cos(2*np.pi*df['hour']/24)
df['dow_sin'] = np.sin(2*np.pi*df['dow']/7)
df['dow_cos'] = np.cos(2*np.pi*df['dow']/7)
df['is_weekend'] = df['dow'] >= 5

feat_cols = ['latitude','longitude','hour','dow','month','is_weekend','hour_sin','hour_cos','dow_sin','dow_cos','vw','vc']
X = df[feat_cols].fillna(0)
y_reg = df['sev']
X_train, X_test, y_train, y_test = train_test_split(X, y_reg, test_size=0.2, random_state=42)

xgb_model = xgb.XGBRegressor(n_estimators=200, max_depth=6, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0)
xgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
xgb_r2 = xgb_model.score(X_test, y_test)
pr(f'XGBoost R²: {xgb_r2:.4f}')

y_vd = df['delta_v_kmh'].reset_index(drop=True)
X_tr2, X_te2, y_tr2, y_te2 = train_test_split(X, y_vd, test_size=0.2, random_state=42)
lgb_model = lgb.LGBMRegressor(n_estimators=200, max_depth=6, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1)
lgb_model.fit(X_tr2, y_tr2, eval_set=[(X_te2, y_te2)])
lgb_r2 = lgb_model.score(X_te2, y_te2)
pr(f'LightGBM R²: {lgb_r2:.4f}')

joblib.dump(xgb_model, os.path.join(OUTPUT, 'xgb_severity.pkl'))
joblib.dump(lgb_model, os.path.join(OUTPUT, 'lgb_velocity.pkl'))

# Predictions
predictions = []
for _, hex_row in h3_stats.iterrows():
    for future_hour in [8, 12, 17, 20]:
        pred_X = pd.DataFrame([{
            'latitude': hex_row['center_lat'], 'longitude': hex_row['center_lon'],
            'hour': future_hour, 'dow': 2, 'month': 3, 'is_weekend': 0,
            'hour_sin': np.sin(2*np.pi*future_hour/24),
            'hour_cos': np.cos(2*np.pi*future_hour/24),
            'dow_sin': np.sin(2*np.pi*2/7), 'dow_cos': np.cos(2*np.pi*2/7),
            'vw': 0.6, 'vc': 1,
        }])
        predictions.append({
            'h3_index': hex_row['h3_index'], 'predicted_hour': future_hour,
            'predicted_severity': round(float(xgb_model.predict(pred_X)[0]), 3),
            'predicted_delta_v': round(float(lgb_model.predict(pred_X)[0]), 2),
            'risk_level': str(hex_row['risk_level']),
        })
pred_df = pd.DataFrame(predictions)
pred_df.to_csv(os.path.join(OUTPUT, 'predictions.csv'), index=False)
pr(f'Predictions: {pred_df["h3_index"].nunique()} hexes x 4 time windows')

# ==============================
# STEP 8: SMART DISPATCH
# ==============================
pr('Step 8: Smart Dispatch Guide...')
h3_stats['velocity_recovery_per_violation'] = h3_stats['avg_delta_v'] / h3_stats['violation_count'].clip(lower=1)
h3_stats = h3_stats.sort_values('velocity_recovery_per_violation', ascending=False)

dispatch_guide = []
for _, row in h3_stats.head(30).iterrows():
    v_recovery = row['avg_delta_v']
    commuters_affected = row['violations_per_day'] * 50
    time_saved = (v_recovery / 35) * 60 * commuters_affected / 60
    dispatch_guide.append({
        'h3_index': row['h3_index'],
        'location': f"{row['center_lat']:.4f}, {row['center_lon']:.4f}",
        'primary_road': row.get('primary_road','unknown'),
        'violations_per_day': round(row['violations_per_day'],1),
        'velocity_recovery_kmh': round(v_recovery,1),
        'estimated_commuters_affected': int(commuters_affected),
        'total_time_saved_hrs': round(time_saved,1),
        'cpi': row['cpi'], 'risk_level': row['risk_level'],
        'police_station': row.get('police_station','unknown'),
    })
dispatch_df = pd.DataFrame(dispatch_guide)
dispatch_df.to_csv(os.path.join(OUTPUT, 'dispatch_guide.csv'), index=False)
pr(f'Dispatch guide: {len(dispatch_df)} zones')

# ==============================
# STEP 9: ECONOMIC IMPACT
# ==============================
pr('Step 9: Economic Impact...')
AVG_SPEED_DEFICIT = df['delta_v_kmh'].mean()
avg_time_loss = df['time_loss_min_per_km'].mean()
daily_violations = len(df) / 150
daily_commuters = daily_violations * 50
daily_hours = daily_commuters * (avg_time_loss * 15 / 60)
daily_fuel = daily_hours * (8.0 / 60)
daily_loss = daily_hours * 250 + daily_fuel * 102

econ = {
    'daily_violations': int(daily_violations),
    'avg_speed_deficit_kmh': round(AVG_SPEED_DEFICIT,1),
    'daily_commuter_hours_wasted': round(daily_hours,1),
    'daily_fuel_wasted_liters': round(daily_fuel,1),
    'daily_productivity_loss_inr': round(daily_hours*250,0),
    'daily_fuel_cost_inr': round(daily_fuel*102,0),
    'total_daily_loss_inr': round(daily_loss,0),
    'annual_loss_inr': round(daily_loss*365,0),
}
pd.DataFrame([econ]).to_csv(os.path.join(OUTPUT, 'economic_impact.csv'), index=False)
pr(f'Daily loss: Rs. {daily_loss:,.0f}, Annual: Rs. {daily_loss*365:,.0f}')

# ==============================
# STEP 10: VISUALIZATION
# ==============================
pr('Step 10: Visualization...')
import folium
from folium.plugins import MarkerCluster

cla, clo = h3_stats['center_lat'].mean(), h3_stats['center_lon'].mean()
m = folium.Map([cla,clo], zoom_start=12, tiles='CartoDB dark_matter')
rc = {'Critical':'red','High':'orange','Medium':'yellow','Low':'green'}

for _, r in h3_stats.iterrows():
    col = rc.get(str(r.get('risk_level','Medium')),'blue')
    boundary = r.get('boundary', [])
    if boundary and len(boundary) > 2:
        folium.Polygon(
            locations=[(lat, lon) for lat, lon in boundary],
            color=col, weight=1, fill=True, fill_color=col, fill_opacity=0.3,
            popup=folium.Popup(
                f"<b>H3:</b> {r['h3_index'][:12]}...<br>"
                f"<b>CPI:</b> {r['cpi']:.3f}<br>"
                f"<b>Risk:</b> {r.get('risk_level','?')}<br>"
                f"<b>Violations:</b> {r['violation_count']}<br>"
                f"<b>ΔV:</b> {r.get('avg_delta_v',0):.1f} km/h<br>"
                f"<b>Road:</b> {r.get('primary_road','?')}<br>"
                f"<b>Station:</b> {r.get('police_station','?')}",
                max_width=250
            )
        ).add_to(m)
m.save(os.path.join(OUTPUT, 'hotspot_map.html'))
pr('Saved hotspot_map.html')

# Route map
def hav(a1,o1,a2,o2):
    a1,o1,a2,o2 = map(radians,[a1,o1,a2,o2])
    return 2*6371*asin(sqrt(sin((a2-a1)/2)**2+cos(a1)*cos(a2)*sin((o2-o1)/2)**2))

c = h3_stats.sort_values('enforcement_priority', ascending=False).copy()
c['used'] = False
routes = []
for pid in range(5):
    route = []
    clat,clon = c.iloc[0]['center_lat'], c.iloc[0]['center_lon']
    for _ in range(8):
        un = c[~c['used']]
        if un.empty: break
        un = un.copy()
        un['d'] = un.apply(lambda r: hav(clat,clon,r['center_lat'],r['center_lon']), axis=1)
        dm = un['d'].max() or 1
        un['sc'] = 0.6*un['enforcement_priority'] + 0.4*(1-un['d']/dm)
        b = un.nlargest(1,'sc').iloc[0]
        c.loc[b.name,'used'] = True
        route.append({'stop':len(route)+1,'violations':int(b['violation_count']),'risk':str(b.get('risk_level','?')),'vio_type':b.get('top_violation','?'),'station':b.get('police_station','?'),'lat':b['center_lat'],'lon':b['center_lon']})
        clat,clon = b['center_lat'], b['center_lon']
    if route:
        routes.append({'patrol':pid+1,'stops':route,'total_vio':sum(s['violations'] for s in route)})

m2 = folium.Map([cla,clo], zoom_start=12, tiles='CartoDB positron')
cols = ['red','blue','green','purple','orange','darkred','darkblue']
for rt in routes:
    pid,col = rt['patrol'], cols[(rt['patrol']-1)%len(cols)]
    coords = [[s['lat'],s['lon']] for s in rt['stops']]
    if len(coords)>1:
        folium.PolyLine(coords,color=col,weight=3,opacity=0.8).add_to(m2)
    for s in rt['stops']:
        folium.Marker([s['lat'],s['lon']],popup=f"Patrol {pid} Stop {s['stop']}",icon=folium.Icon(color=col,icon='info-sign')).add_to(m2)
m2.save(os.path.join(OUTPUT, 'route_map.html'))
pr('Saved route_map.html')

# GeoJSON for Kepler.gl
hex_features = []
for _, r in h3_stats.iterrows():
    boundary = r.get('boundary', [])
    if boundary and len(boundary) > 2:
        coords = [[lon, lat] for lat, lon in boundary]
        coords.append(coords[0])
        hex_features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [coords]},
            'properties': {
                'h3_index': r['h3_index'], 'cpi': float(r['cpi']),
                'risk_level': str(r.get('risk_level','Unknown')),
                'violation_count': int(r['violation_count']),
                'violations_per_day': float(r['violations_per_day']),
                'delta_v': float(r.get('avg_delta_v',0)),
                'road': str(r.get('primary_road','unknown')),
                'station': str(r.get('police_station','unknown')),
            }
        })
geojson = {'type': 'FeatureCollection', 'features': hex_features}
with open(os.path.join(OUTPUT, 'h3_hexes.geojson'), 'w') as f:
    json.dump(geojson, f)
pr(f'Saved h3_hexes.geojson ({len(hex_features)} hexagons)')

# Save CSVs
h3_stats.to_csv(os.path.join(OUTPUT, 'h3_analysis.csv'), index=False)

# ==============================
# SUMMARY
# ==============================
import glob
files = glob.glob(os.path.join(OUTPUT,'*'))
pr('='*70)
pr('  GridLock Pipeline COMPLETE!')
pr('='*70)
pr(f'  Violations: {len(df):,}')
pr(f'  H3 Hexagons: {len(h3_stats)}')
pr(f'  Critical zones: {len(h3_stats[h3_stats["risk_level"]=="Critical"])}')
pr(f'  High risk zones: {len(h3_stats[h3_stats["risk_level"]=="High"])}')
pr(f'  Avg CPI: {h3_stats["cpi"].mean():.3f}')
pr(f'  Avg Speed Deficit: {AVG_SPEED_DEFICIT:.1f} km/h')
pr(f'  Daily Economic Loss: Rs. {daily_loss:,.0f}')
pr(f'  XGBoost R²: {xgb_r2:.4f}')
pr(f'  LightGBM R²: {lgb_r2:.4f}')
pr(f'  Output files ({len(files)}):')
for f in sorted(files):
    pr(f'    {os.path.basename(f)}: {os.path.getsize(f)/1024:.1f}KB')
pr('='*70)
