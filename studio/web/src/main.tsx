import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Library } from "./Library";
import { Studio } from "./Studio";
import "./styles.css";

const BASE = "/studio";

export function navigate(path: string) {
  window.history.pushState(null, "", BASE + path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const project = path.match(/^\/studio\/p\/([^/]+)\/?$/);
  if (project) return <Studio key={project[1]} id={decodeURIComponent(project[1])} />;
  return <Library />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
