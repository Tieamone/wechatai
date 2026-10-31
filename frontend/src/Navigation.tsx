import {PollingContext} from './usePolling';
import type {ReactNode} from 'react';
export const groups=[
  {name:'首页',icon:'◈',pages:[['home','运行概况']]},
  {name:'聊天与记忆',icon:'▤',pages:[['memory/search','寻找历史'],['memory/library','记忆库'],['memory/people','人物资料']]},
  {name:'提醒中心',icon:'♧',pages:[['reminders/list','提醒与记录'],['reminders/watch','特别关注']]},
  {name:'任务管理',icon:'◷',pages:[['tasks/automatic','新消息自动处理'],['tasks/history','历史初始化']]},
  {name:'设置与帮助',icon:'⚙',pages:[['settings/model','模型配置'],['settings/budgets','调用预算'],['settings/notifications','通知与备份'],['settings/privacy','隐私与映射'],['settings/import','手工导入'],['settings/help','使用帮助']]},
];
export function currentRoute(){const route=window.location.hash.replace(/^#\/?/,'');return groups.some(g=>g.pages.some(p=>p[0]===route))?route:'home'}
export function Page({id,route,children}:{id:string;route:string;children:ReactNode}){return <PollingContext.Provider value={id===route}><div className="workspace-page" hidden={id!==route} data-page={id}>{children}</div></PollingContext.Provider>}
export function Navigation({route,pending=0}:{route:string;pending?:number}){return <nav className="sidebar" aria-label="主导航"><a href="#/home" className="brand"><span>记</span><div>聊天记忆助手<small>你的本地工作台</small></div></a><div className="nav-items">{groups.map(g=><a key={g.name} href={'#/'+g.pages[0][0]} aria-current={g.pages.some(p=>p[0]===route)?'page':undefined}><span aria-hidden="true">{g.icon}</span>{g.name}{g.name==='聊天与记忆'&&pending>0&&<small aria-label="待核对记忆数量"> · {pending}</small>}</a>)}</div><div className="sidebar-note"><span className="live-dot"/> 本机运行<p>关闭命令行窗口<br/>采集与机器人一起停止</p></div></nav>}
