use anyhow::Result;

use crate::{call_set_model, load_inventory, ModelInfo, ModelProvider};

use super::modal::{pick_choice_modal, prompt_line_modal};
use super::state::{PanelView, Role, TerminalApp};
use super::TerminalSurface;

pub(super) fn handle_model_command(app: &mut TerminalApp, terminal: &mut TerminalSurface) -> Result<()> {
    app.panel = PanelView::Models;
    app.activity = "choosing model".to_string();
    app.refresh(None);
    terminal.render(app, None)?;

    let inventory = match load_inventory() {
        Ok(inventory) if inventory.providers.is_empty() => {
            app.activity = "idle".to_string();
            app.push(Role::Error, "/model", "No providers were found in the model inventory.");
            return Ok(());
        }
        Ok(inventory) => inventory,
        Err(err) => {
            app.activity = "idle".to_string();
            app.push(Role::Error, "/model", &format!("Could not load model inventory: {err}"));
            return Ok(());
        }
    };

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

    let provider = inventory.providers[provider_index].clone();
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
            app.activity = "idle".to_string();
            app.panel = PanelView::Models;
            app.refresh(None);
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

fn cancel_model_selection(app: &mut TerminalApp) {
    app.activity = "idle".to_string();
    app.panel = PanelView::Models;
    app.refresh(None);
    app.push(Role::Command, "/model", "Model selection cancelled.");
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
    let configured = if provider.configured { "ready" } else { "setup" };
    let hint = if provider.hint.is_empty() {
        String::new()
    } else {
        format!(" - {}", provider.hint)
    };
    format!(
        "{} ({}) - {} model(s), {} {}{}",
        provider.name,
        provider.id,
        provider.models.len(),
        provider.status,
        configured,
        hint
    )
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
