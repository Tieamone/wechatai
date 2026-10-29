export function Processing({label='AI 正在提炼，请稍候'}:{label?:string}){
  return <div className="processing" aria-live="polite"><span className="activity-dot"/>{label}<div className="motion-track" role="progressbar" aria-label={label}><span/></div><small>完成后自动更新 · 此动画表示正在处理，不是完成百分比</small></div>;
}
