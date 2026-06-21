# GridLock - AI Parking Intelligence Platform

## Problem
298K+ parking violations in Bengaluru choke intersections and carriageways. Enforcement is patrol-based and reactive with no intelligence on WHERE violations cluster, WHEN they peak, or HOW they impact traffic.

## Solution
AI-driven platform with 5 engines:
1. **Hotspot Detection** - DBSCAN clustering identifies violation zones
2. **Severity Scoring** - Per-violation and per-zone impact quantification
3. **Risk Classification** - ML model classifies zones (Low/Medium/High/Critical)
4. **Predictive Intelligence** - GradientBoosting predicts future risk
5. **Enforcement Optimizer** - Greedy algorithm generates optimal patrol routes

## How to Run (Kaggle)

### Setup
1. Create a new Kaggle notebook
2. Upload the CSV file as a dataset or add to input
3. Upload all 7 notebooks (00-06) in order
4. Set kernel to Python 3

### Execute in Order
| # | Notebook | What It Does |
|---|----------|-------------|
| 0 | `00_setup.ipynb` | Install packages, set paths |
| 1 | `01_data_loading.ipynb` | Load & explore 298K records |
| 2 | `02_feature_engineering.ipynb` | Clean, features, severity scoring |
| 3 | `03_hotspot_detection.ipynb` | DBSCAN clustering, density heatmap |
| 4 | `04_congestion_impact.ipynb` | Risk levels, junction analysis |
| 5 | `05_route_optimizer.ipynb` | Patrol routes, recommendations |
| 6 | `06_visualization.ipynb` | Maps, charts, dashboard |

### Outputs
All outputs saved to `/kaggle/working/outputs/`:
- `processed_data.parquet` - Cleaned data with all features
- `cluster_analysis.csv` - Hotspot zones with risk levels
- `spatial_density.csv` - Density heatmap grid
- `junction_impact.csv` - Junction-level analysis
- `patrol_routes.csv` - Optimized patrol routes
- `zone_recommendations.csv` - Per-zone enforcement recommendations
- `risk_classifier.pkl` - Trained ML model
- `hotspot_map.html` - Interactive Folium hotspot map
- `route_map.html` - Interactive patrol route map
- Various `.html` Plotly charts

## Architecture
```
Raw Data (298K) → Feature Engineering → DBSCAN Clustering → Risk Classification
                                            ↓
                                    Spatial Density → Composite Ranking
                                            ↓
                                    Route Optimization → Zone Recommendations
                                            ↓
                                    Interactive Dashboard (Folium + Plotly)
```

## Key Metrics
- **298K** violations analyzed
- **~50-100** hotspot zones identified
- **5** optimized patrol routes
- **60-80%** zone coverage per patrol cycle
- Severity scoring combines: violation type × vehicle size × time-of-day × multi-violation

## Tech Stack
- **Processing**: Pandas, NumPy (chunked for memory)
- **ML**: Scikit-learn (DBSCAN, RandomForest, GradientBoosting)
- **Maps**: Folium (interactive heatmaps + markers)
- **Charts**: Plotly (bar, scatter, pie, line)
- **Optimization**: Custom greedy algorithm with haversine distance
