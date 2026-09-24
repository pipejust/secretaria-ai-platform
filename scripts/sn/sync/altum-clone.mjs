// Empezar a trabajar en un proyecto de Altum: "clóname el proyecto Altum".
// Altum guarda el repositorio de cada proyecto en repo_url; aquí se busca el proyecto por su nombre
// (sin tildes ni mayúsculas, por palabras sueltas) y se clona en una carpeta con su clave corta.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';

// Clave corta y escribible del proyecto: "Medición de Afluencia" -> "medicion-de-afluencia".
export function projectKey(name) {
  return String(name || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// Busca por clave exacta, luego por texto contenido y, si no, por palabras en común.
// Devuelve { match } si hay una sola, { candidates } si hay varias, {} si ninguna.
export function findProject(projects, query) {
  const wanted = projectKey(query);
  if (!wanted) return {};
  const conKey = projects.map((p) => ({ ...p, key: p.key || projectKey(p.name) }));
  const exacta = conKey.filter((p) => p.key === wanted);
  const contiene = conKey.filter((p) => p.key.includes(wanted) || wanted.includes(p.key));
  const palabras = wanted.split('-').filter((w) => w.length > 2);
  const porPalabra = conKey.filter((p) => palabras.some((w) => p.key.includes(w)));
  const lista = exacta.length ? exacta : contiene.length ? contiene : porPalabra;
  if (lista.length === 1) return { match: lista[0] };
  if (lista.length > 1) return { candidates: lista };
  return {};
}

// ¿Dónde queda el proyecto? Dos formas, según lo que pida la persona:
//   dentro de una carpeta madre  → <carpeta>/<clave del proyecto>   (crea la carpeta del proyecto)
//   en una ruta exacta           → esa misma ruta, con el contenido del repositorio adentro
// Si la carpeta madre YA se llama como el proyecto, no se anida otra igual dentro.
// Cuando el proyecto tiene VARIOS repositorios, la carpeta se llama como el repositorio elegido
// (si no, dos repositorios del mismo proyecto pelearían por la misma carpeta).
export function targetDir(project, parent, { exact = false, repo = null } = {}) {
  const variosRepos = (project.repos?.length || 0) > 1;
  const clave = variosRepos && repo?.name ? projectKey(repo.name.split('/').pop()) : project.key || projectKey(project.name);
  const destino = path.resolve(parent);
  if (exact || path.basename(destino) === clave) return destino;
  return path.join(destino, clave);
}

export function alreadyThere(dir) {
  return existsSync(dir) && readdirSync(dir).length > 0;
}

export function cloneProject(project, parent, opciones = {}) {
  const dir = targetDir(project, parent, opciones);
  const url = opciones.repo?.url || project.repo;
  if (!url) throw new Error(`el proyecto "${project.name}" no tiene repositorio registrado en Altum: regístralo en su ficha ("Repositorios") y vuelve a intentar`);
  if (alreadyThere(dir)) return { dir, cloned: false };
  execFileSync('git', ['clone', url, dir], { windowsHide: true, stdio: 'inherit' });
  return { dir, cloned: true };
}

// ¿Cuál de los repositorios del proyecto? Con uno solo, ese. Con varios, hay que elegir:
// exacto por nombre ("empresa/app" o "app"), y si no coincide, se devuelven todos para preguntar.
export function findRepo(project, query) {
  const repos = project.repos?.length ? project.repos : (project.repo ? [{ name: project.repo, url: project.repo }] : []);
  if (repos.length <= 1) return { repo: repos[0] || null };
  if (!query) return { choices: repos };
  const buscado = projectKey(query);
  const exacto = repos.filter((r) => projectKey(r.name) === buscado || projectKey(r.name.split('/').pop()) === buscado);
  const parecido = repos.filter((r) => projectKey(r.name).includes(buscado));
  const lista = exacto.length ? exacto : parecido;
  return lista.length === 1 ? { repo: lista[0] } : { choices: lista.length ? lista : repos };
}

// ¿Qué proyectos de Altum usan este repositorio? Se compara proveedor + nombre ("org/repo"),
// sin importar https/ssh, ".git" ni mayúsculas. Un mismo repositorio puede servir a varios proyectos.
export function projectsForRepo(projects, remote, identificar) {
  if (!remote) return [];
  const clave = (url) => { const r = identificar(url); return `${r.provider}:${r.name.toLowerCase()}`; };
  const mio = clave(remote);
  return projects.filter((p) => (p.repos || []).some((r) => clave(r.url) === mio));
}
