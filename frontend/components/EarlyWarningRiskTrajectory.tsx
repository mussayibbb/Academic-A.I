type TrendPoint = { weekStart: string; risk: number };
type RiskTrajectoryProps = {
  trend: TrendPoint[];
  forecast: { noAction: number; withPlan: number };
  currentRisk: number;
  animateIn: boolean;
};

const riskColor = (risk: number) =>
  risk >= 0.75 ? 'var(--hi)' : risk >= 0.4 ? 'var(--md)' : 'var(--lo)';

export default function EarlyWarningRiskTrajectory({
  trend,
  forecast,
  currentRisk,
  animateIn,
}: RiskTrajectoryProps) {
  const historyPoints = trend.slice(-8).map((point, index, points) => ({
    x: points.length === 1 ? 280 : 10 + index * (270 / (points.length - 1)),
    y: 116 - Math.max(0, Math.min(1, point.risk)) * 96,
  }));
  const nowPoint = trend.length >= 3 && historyPoints.length
    ? historyPoints[historyPoints.length - 1]
    : { x: 280, y: 116 - Math.max(0, Math.min(1, currentRisk)) * 96 };
  const color = riskColor(currentRisk);
  const linePath = historyPoints.map((point, index) =>
    `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`,
  ).join('');
  const areaPath = historyPoints.length >= 3
    ? `${linePath}L${nowPoint.x.toFixed(1)} 120L${historyPoints[0].x.toFixed(1)} 120Z`
    : '';
  const forecastPath = (risk: number) =>
    `M${nowPoint.x.toFixed(1)} ${nowPoint.y.toFixed(1)}L390 ${(116 - Math.max(0, Math.min(1, risk)) * 96).toFixed(1)}`;

  return <div className="ew-card ew-tr-card">
    <h3>Risk trajectory</h3>
    <p className="ew-lead">Predicted risk for the last 8 weeks and a 2-week forecast.</p>
    <svg viewBox="0 0 400 136" role="img" aria-label="Risk trajectory with two-week forecast">
      <line x1="10" x2="390" y1="44" y2="44" stroke="var(--hi)" strokeOpacity=".4" strokeDasharray="3 4" />
      <text x="390" y="39" fontSize="9" textAnchor="end" fill="var(--hi)">Danger zone starts at 75%</text>
      {areaPath && <path className={`ew-area ${animateIn ? 'animate' : ''}`} d={areaPath} fill={color} fillOpacity=".14" />}
      {historyPoints.length >= 3 && <path className={`ew-trend-line ${animateIn ? 'animate' : ''}`} pathLength="1" d={linePath} stroke={color} />}
      <path className={`ew-forecast ew-forecast-red ${animateIn ? 'animate' : ''}`} d={forecastPath(forecast.noAction)} />
      <path className={`ew-forecast ew-forecast-green ${animateIn ? 'animate' : ''}`} d={forecastPath(forecast.withPlan)} />
      <circle className={`ew-now-dot ${animateIn ? 'animate' : ''}`} cx={nowPoint.x} cy={nowPoint.y} r="5" fill={color} />
      <text x="10" y="132" fontSize="10" textAnchor="start" fill="var(--ew-mute)">8 weeks ago</text>
      <text x={nowPoint.x} y="132" fontSize="10" textAnchor="middle" fill="var(--ew-mute)">Now</text>
      <text x="390" y="132" fontSize="10" textAnchor="end" fill="var(--ew-mute)">In 2 weeks</text>
    </svg>
    {trend.length < 3 && <p className="ew-history-note">Weekly history is still building. The forecast is based on the latest data.</p>}
    <div className="ew-legend">
      <span><i className="ew-legend-red" />No action: {Math.round(forecast.noAction * 100)}%</span>
      <span><i className="ew-legend-green" />With the plan: {Math.round(forecast.withPlan * 100)}%</span>
    </div>
  </div>;
}
