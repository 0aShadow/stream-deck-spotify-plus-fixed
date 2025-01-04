import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";
import typescript from "@rollup/plugin-typescript";
import path from "node:path";
import url from "node:url";
import fs from "node:fs";
import { execSync } from "child_process";

const isWatching = !!process.env.ROLLUP_WATCH;
const sdPlugin = "fr.dbenech.spotify-plus.sdPlugin";

/**
 * @type {import('rollup').RollupOptions}
 */
const config = {
	input: "src/plugin.ts",
	output: {
		file: `${sdPlugin}/bin/plugin.js`,
		sourcemap: isWatching,
		sourcemapPathTransform: (relativeSourcePath, sourcemapPath) => {
			return url.pathToFileURL(path.resolve(path.dirname(sourcemapPath), relativeSourcePath)).href;
		}
	},
	plugins: [
		{
			name: "watch-externals",
			buildStart: function () {
				this.addWatchFile(`${sdPlugin}/manifest.json`);
				fs.readdirSync('backend').forEach(file => {
					this.addWatchFile(`backend/${file}`);
				});
			},
		},
		{
			name: 'copy-python-backend',
			buildStart() {
				const srcDir = 'backend';
				const destDir = `${sdPlugin}/bin/backend`;

				try {
					// Create destination directory
					fs.mkdirSync(destDir, { recursive: true });

					// Copy all files from backend directory
					fs.readdirSync(srcDir).forEach(file => {
						const srcPath = path.join(srcDir, file);
						const destPath = path.join(destDir, file);

						if (fs.lstatSync(srcPath).isDirectory()) {
							fs.cpSync(srcPath, destPath, { recursive: true });
						} else {
							fs.copyFileSync(srcPath, destPath);
						}
					});

					console.log('Successfully copied backend folder to bin directory');

					// Create virtual environment if it doesn't exist
					if (!fs.existsSync(`${destDir}/venv`)) {
						console.log('Creating Python virtual environment...');
						execSync(`python -m venv ${destDir}/venv`);
					}

					// Update pip and install requirements
					console.log('Installing Python dependencies...');
					const pythonCmd = process.platform === 'win32'
						? path.join(destDir, 'venv', 'Scripts', 'python.exe')
						: path.join(destDir, 'venv', 'bin', 'python');

					// Upgrade pip first
					execSync(`"${pythonCmd}" -m pip install --upgrade pip`, { stdio: 'inherit' });

					// Install requirements with verbose output
					execSync(`"${pythonCmd}" -m pip install -r "${path.join(srcDir, 'requirements.txt')}"`, {
						stdio: 'inherit'
					});

					console.log('Python environment setup completed');
				} catch (error) {
					console.error('Error in plugin setup:', error);
					throw error;
				}
			}
		},
		typescript({
			mapRoot: isWatching ? "./" : undefined
		}),
		nodeResolve({
			browser: false,
			exportConditions: ["node"],
			preferBuiltins: true
		}),
		commonjs(),
		!isWatching && terser(),
		{
			name: "emit-module-package-file",
			generateBundle() {
				this.emitFile({ fileName: "package.json", source: `{ "type": "module" }`, type: "asset" });
			}
		}
	]
};

export default config;
