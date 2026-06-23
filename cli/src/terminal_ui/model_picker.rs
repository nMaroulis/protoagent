use anyhow::Result;

use crate::{
    call_add_api_key, call_set_model, load_inventory_with_validation, ModelInfo, ModelProvider,
};

use super::modal::{draw_loading_modal, pick_choice_modal, prompt_line_modal, prompt_secret_modal};
use super::state::{PanelView, Role, TerminalApp};
use super::TerminalSurface;

pub(super) fn handle_model_command(app: &mut TerminalApp, terminal: &mut TerminalSurface) -> Result<()> {
    app.panel = PanelView::Models;
    let Some(inventory) = load_inventory_with_feedback(
        app,
        terminal,
        "/model",
        "Loading Providers",
        "Discovering local models and configured providers.",
    )? else {
        return Ok(());
    };
    if inventory.providers.is_empty() {
        app.activity = "idle".to_string();
        app.push(Role::Error, "/model", "No providers were found in the model inventory.");
        return Ok(());
    }
    app.activity = "choosing model".to_string();

    let provider_choices = inventory
        .providers
        .iter()
        .map(provider_choice_label)
        .collect::<Vec<_>>();
    let initial_provider = inventory
        .providers
        .iter()
        .position(|provider| provider.id == inventory.active_provider)
        .unwrap_or(0);
    let active_model = if inventory.active_model.is_empty() {
        "not selected"
    } else {
        inventory.active_model.as_str()
    };
    let Some(provider_index) = pick_choice_modal(
        terminal,
        app,
        "Choose Provider",
        &[
            format!("Active: {} / {}", inventory.active_provider, active_model),
            "Type to filter. Enter selects. Esc cancels.".to_string(),
        ],
        &provider_choices,
        initial_provider,
    )?
    else {
        cancel_model_selection(app);
        return Ok(());
    };

    let Some(provider) = ensure_api_key_for_provider(terminal, app, inventory.providers[provider_index].clone())?
    else {
        cancel_model_selection(app);
        return Ok(());
    };
    let active_model_for_provider = if provider.id == inventory.active_provider {
        inventory.active_model.as_str()
    } else {
        ""
    };
    let Some(model) = choose_model_value(terminal, app, &provider, active_model_for_provider)? else {
        cancel_model_selection(app);
        return Ok(());
    };
    let model = model.trim().to_string();
    if model.is_empty() {
        app.activity = "idle".to_string();
        app.push(Role::Error, "/model", "Model id cannot be empty.");
        return Ok(());
    }

    let base_url = if provider_needs_base_url(&provider) {
        match prompt_line_modal(
            terminal,
            app,
            "Provider Base URL",
            &[
                format!("Provider: {} ({})", provider.name, provider.id),
                "Leave blank to keep provider defaults.".to_string(),
                "Enter saves. Esc cancels.".to_string(),
            ],
            &provider.base_url,
        )? {
            Some(value) => Some(value.trim().to_string()),
            None => {
                cancel_model_selection(app);
                return Ok(());
            }
        }
    } else {
        None
    };

    app.activity = "saving model".to_string();
    terminal.render(app, None)?;
    match call_set_model(provider.id.clone(), model.clone(), base_url) {
        Ok(_) => {
            app.panel = PanelView::Models;
            let _ = load_inventory_with_feedback(
                app,
                terminal,
                "/model",
                "Applying Model",
                "Refreshing the active model and provider status.",
            )?;
            app.activity = "idle".to_string();
            app.push(
                Role::Command,
                "/model",
                &format!("Active model changed to {} / {}.", provider.id, model),
            );
        }
        Err(err) => {
            app.activity = "idle".to_string();
            app.push(Role::Error, "/model", &format!("Could not save model: {err:?}"));
        }
    }
    Ok(())
}

pub(super) fn handle_key_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    preselected_provider: Option<&str>,
) -> Result<()> {
    app.panel = PanelView::Models;
    let Some(inventory) = load_inventory_with_feedback(
        app,
        terminal,
        "/key",
        "Loading Providers",
        "Checking API providers and saved key status.",
    )? else {
        return Ok(());
    };
    app.activity = "setting api key".to_string();
    let api_providers = inventory
        .providers
        .iter()
        .filter(|provider| provider.kind == "api" || provider.id == "openai-compatible")
        .cloned()
        .collect::<Vec<_>>();
    if api_providers.is_empty() {
        app.activity = "idle".to_string();
        app.push(Role::Error, "/key", "No API-key providers are available.");
        return Ok(());
    }

    let provider = match preselected_provider {
        Some(id) => match api_providers.iter().find(|provider| provider.id == id) {
            Some(provider) => provider.clone(),
            None => {
                app.activity = "idle".to_string();
                app.push(Role::Error, "/key", &format!("{id} is not an API-key provider."));
                return Ok(());
            }
        },
        None => {
            let choices = api_providers
                .iter()
                .map(provider_choice_label)
                .collect::<Vec<_>>();
            let Some(index) = pick_choice_modal(
                terminal,
                app,
                "API Key Provider",
                &[
                    "Choose the provider whose key you want to store.".to_string(),
                    "Keys are stored in the ProtoAgent config unless provided by env.".to_string(),
                ],
                &choices,
                0,
            )?
            else {
                app.activity = "idle".to_string();
                app.push(Role::Command, "/key", "API key setup cancelled.");
                return Ok(());
            };
            api_providers[index].clone()
        }
    };

    let Some(updated) = prompt_and_store_api_key(terminal, app, provider)? else {
        app.activity = "idle".to_string();
        app.push(Role::Command, "/key", "API key setup cancelled.");
        return Ok(());
    };
    app.activity = "idle".to_string();
    app.panel = PanelView::Models;
    app.push(
        Role::Command,
        "/key",
        &format!("Stored key for {}. {}", updated.name, key_status_sentence(&updated)),
    );
    Ok(())
}

fn cancel_model_selection(app: &mut TerminalApp) {
    app.activity = "idle".to_string();
    app.panel = PanelView::Models;
    app.models_loading = false;
    app.push(Role::Command, "/model", "Model selection cancelled.");
}

fn ensure_api_key_for_provider(
    terminal: &mut TerminalSurface,
    app: &mut TerminalApp,
    provider: ModelProvider,
) -> Result<Option<ModelProvider>> {
    if !provider_needs_api_key_prompt(&provider) {
        return Ok(Some(provider));
    }
    app.push(
        Role::Command,
        "/model",
        &format!("{} needs an API key before model selection.", provider.name),
    );
    let Some(updated) = prompt_and_store_api_key(terminal, app, provider)? else {
        return Ok(None);
    };
    if updated.key_status == "invalid" {
        app.push(
            Role::Error,
            "/model",
            &format!("{} rejected that API key. Key setup stopped.", updated.name),
        );
        return Ok(None);
    }
    app.push(
        Role::Command,
        "/model",
        &format!("API key ready for {}. Continuing to model selection.", updated.name),
    );
    Ok(Some(updated))
}

fn prompt_and_store_api_key(
    terminal: &mut TerminalSurface,
    app: &mut TerminalApp,
    provider: ModelProvider,
) -> Result<Option<ModelProvider>> {
    let Some(api_key) = prompt_secret_modal(
        terminal,
        app,
        "API Key",
        &[
            format!("Provider: {} ({})", provider.name, provider.id),
            format!("Current key: {}", provider_key_line(&provider)),
            provider_setup_hint(&provider),
            "Enter saves. Esc cancels.".to_string(),
        ],
    )?
    else {
        return Ok(None);
    };
    if api_key.trim().is_empty() {
        return Ok(None);
    }

    call_add_api_key(provider.id.clone(), api_key)
        .map_err(|err| anyhow::anyhow!("Python config error: {err:?}"))?;
    let Some(inventory) = load_inventory_with_feedback(
        app,
        terminal,
        "/key",
        "Validating Key",
        "Checking provider access with the stored key.",
    )? else {
        return Ok(None);
    };
    let updated = inventory
        .providers
        .into_iter()
        .find(|item| item.id == provider.id)
        .unwrap_or(provider);
    Ok(Some(updated))
}

fn load_inventory_with_feedback(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    title: &str,
    detail: &str,
) -> Result<Option<crate::ModelInventory>> {
    app.panel = PanelView::Models;
    app.models_loading = true;
    app.activity = "loading model inventory".to_string();
    terminal.render(app, None)?;
    draw_loading_modal(
        title,
        &[
            detail.to_string(),
            "Ollama / LM Studio / llama.cpp     loading".to_string(),
            "Cloud providers / key status       checking".to_string(),
        ],
    )?;
    match load_inventory_with_validation(true) {
        Ok(inventory) => {
            app.apply_model_inventory(&inventory);
            Ok(Some(inventory))
        }
        Err(err) => {
            app.models_loading = false;
            app.activity = "idle".to_string();
            app.push(Role::Error, command, &format!("Could not load model inventory: {err}"));
            Ok(None)
        }
    }
}

fn choose_model_value(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    provider: &ModelProvider,
    active_model: &str,
) -> Result<Option<String>> {
    if provider.models.is_empty() {
        return prompt_line_modal(
            terminal,
            app,
            "Custom Model",
            &[
                format!("Provider: {} ({})", provider.name, provider.id),
                if provider.hint.is_empty() {
                    "Enter a model id, local name, or .gguf path.".to_string()
                } else {
                    provider.hint.clone()
                },
                "Enter confirms. Esc cancels.".to_string(),
            ],
            active_model,
        );
    }

    let mut model_choices = provider
        .models
        .iter()
        .map(model_choice_label)
        .collect::<Vec<_>>();
    model_choices.push("Custom model id or path".to_string());
    let initial_model = provider
        .models
        .iter()
        .position(|model| model.id == active_model)
        .unwrap_or(0);
    let Some(model_index) = pick_choice_modal(
        terminal,
        app,
        "Choose Model",
        &[
            format!("Provider: {} ({})", provider.name, provider.id),
            format!("Visible models: {}", provider.models.len()),
            "Type to filter. Enter selects. Esc cancels.".to_string(),
        ],
        &model_choices,
        initial_model,
    )?
    else {
        return Ok(None);
    };

    if model_index == provider.models.len() {
        return prompt_line_modal(
            terminal,
            app,
            "Custom Model",
            &[
                format!("Provider: {} ({})", provider.name, provider.id),
                "Enter a model id, local name, or .gguf path.".to_string(),
                "Enter confirms. Esc cancels.".to_string(),
            ],
            active_model,
        );
    }

    Ok(Some(provider.models[model_index].id.clone()))
}

fn provider_needs_base_url(provider: &ModelProvider) -> bool {
    provider.kind.contains("server") || provider.kind.contains("compatible")
}

fn provider_choice_label(provider: &ModelProvider) -> String {
    let configured = if provider.configured { "[READY]" } else { "[SETUP]" };
    let hint = if provider.hint.is_empty() {
        String::new()
    } else {
        format!(" - {}", provider.hint)
    };
    format!(
        "{} ({}) - {} model(s) {} {}{}",
        provider.name,
        provider.id,
        provider.models.len(),
        provider_status_badges(provider),
        configured,
        hint
    )
}

fn provider_status_badges(provider: &ModelProvider) -> String {
    let mut badges = vec![format!("[{}]", provider.status.to_uppercase())];
    if provider.kind == "api" || provider.api_key_set || !provider.key_status.is_empty() {
        badges.push(format!("[KEY:{}]", provider_key_badge(provider)));
    }
    badges.join(" ")
}

fn provider_needs_api_key_prompt(provider: &ModelProvider) -> bool {
    provider.kind == "api"
        && (!provider.api_key_set || matches!(provider.key_status.as_str(), "missing" | "invalid"))
}

fn provider_key_badge(provider: &ModelProvider) -> &'static str {
    match provider.key_status.as_str() {
        "valid" => "VALID",
        "invalid" => "INVALID",
        "missing" => "MISSING",
        "unverified" => "UNVERIFIED",
        "not-required" => "NOT-REQUIRED",
        "set" => "SET",
        "" if provider.api_key_set => "SET",
        "" => "N/A",
        _ => "UNKNOWN",
    }
}

fn provider_key_line(provider: &ModelProvider) -> String {
    let source = if provider.key_source.is_empty() {
        "none"
    } else {
        provider.key_source.as_str()
    };
    if provider.env_key.is_empty() {
        format!("{} via {source}", provider_key_badge(provider))
    } else {
        format!("{} via {source} (env: {})", provider_key_badge(provider), provider.env_key)
    }
}

fn provider_setup_hint(provider: &ModelProvider) -> String {
    if !provider.hint.is_empty() {
        return provider.hint.clone();
    }
    if provider.env_key.is_empty() {
        "Paste the provider API key. It will be stored in ProtoAgent config.".to_string()
    } else {
        format!(
            "Paste a key, or cancel and export {} before launching ProtoAgent.",
            provider.env_key
        )
    }
}

fn key_status_sentence(provider: &ModelProvider) -> String {
    match provider.key_status.as_str() {
        "valid" => "The key validated successfully.".to_string(),
        "invalid" => "The provider rejected the key.".to_string(),
        "unverified" => "The key is stored, but validation was inconclusive.".to_string(),
        "set" => "The key is stored.".to_string(),
        _ => format!("Key status: {}.", provider_key_badge(provider)),
    }
}

fn model_choice_label(model: &ModelInfo) -> String {
    let size = if model.size_label.is_empty() {
        String::new()
    } else {
        format!(" [{}]", model.size_label)
    };
    let source = if model.source.is_empty() {
        String::new()
    } else {
        format!(" via {}", model.source)
    };
    let modified = if model.modified_at.is_empty() {
        String::new()
    } else {
        format!(" {}", model.modified_at)
    };
    format!("{}{}{}{}", model.id, size, source, modified)
}
