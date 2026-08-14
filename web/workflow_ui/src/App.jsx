import { useEffect, useState } from 'react';
import WorkflowPage from './pages/WorkflowPage';
import QaBoardPage from './pages/QaBoardPage';

function getRoute() {
  return (window.location.hash || '#/workflow').replace('#/', '').split('/')[0];
}

// Hash 路由：FTS WorkFlow UI 顶层入口
export default function App() {
  const [route, setRoute] = useState(getRoute());

  useEffect(() => {
    const on = () => setRoute(getRoute());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          FTS <b>WorkFlow</b>
        </div>
        <nav>
          <a href="#/workflow" className={route === 'workflow' || route === '' ? 'nav-on' : ''}>
            工作流
          </a>
          <a href="#/qa" className={route === 'qa' ? 'nav-on' : ''}>
            质检看板
          </a>
        </nav>
      </header>
      <main className="content">{route === 'qa' ? <QaBoardPage /> : <WorkflowPage />}</main>
    </div>
  );
}
