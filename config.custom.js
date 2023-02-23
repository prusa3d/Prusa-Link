const webpackConfig = require("./webpack.config");

module.exports = (env, args) => {
    const config = {
        PRINTER_NAME: "Original Prusa i3",
        PRINTER_TYPE: "fdm",

        WITH_API_KEY_SETTING: true,
        WITH_SETTINGS: true,
        WITH_CAMERAS: true,
        WITH_CONTROLS: true,
        WITH_LOGS: true,
        WITH_V1_API: true,
        WITH_TELEMETRY_NOZZLE_DIAMETER: true,
	WITH_SYSTEM_UPDATES: true,
        ...env,
    };
    return webpackConfig(config, args);
}
