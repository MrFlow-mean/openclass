import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const checks = [
  {
    path: "apps/web/src/components/course-studio.tsx",
    maxLines: 1000,
    level: "error",
  },
  {
    path: "apps/web/src/components/course-studio",
    maxLines: 1200,
    level: "warn",
  },
  {
    path: "apps/web/src/hooks/course-studio",
    maxLines: 1200,
    level: "warn",
  },
  {
    path: "apps/api/app/services",
    maxLines: 1300,
    level: "warn",
  },
];

function lineCount(filePath) {
  return fs.readFileSync(filePath, "utf8").split("\n").length;
}

function filesUnder(target) {
  const absolute = path.join(root, target);
  if (!fs.existsSync(absolute)) {
    return [];
  }
  const stat = fs.statSync(absolute);
  if (stat.isFile()) {
    return [target];
  }
  return fs.readdirSync(absolute, { withFileTypes: true }).flatMap((entry) => {
    const child = path.join(target, entry.name);
    if (entry.isDirectory()) {
      return filesUnder(child);
    }
    return /\.(ts|tsx)$/.test(entry.name) ? [child] : [];
  });
}

let failed = false;
for (const check of checks) {
  for (const file of filesUnder(check.path)) {
    const count = lineCount(path.join(root, file));
    if (count <= check.maxLines) {
      continue;
    }
    const message = `${file} has ${count} lines; target is ${check.maxLines}.`;
    if (check.level === "error") {
      failed = true;
      console.error(`[file-size] ${message}`);
    } else {
      console.warn(`[file-size] ${message}`);
    }
  }
}

if (failed) {
  process.exit(1);
}
