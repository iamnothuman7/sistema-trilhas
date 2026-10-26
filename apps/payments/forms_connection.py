from django import forms


class MercadoPagoDraftForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput())
    environment = forms.ChoiceField(label="Como você quer usar a conta?", choices=(("sandbox", "Fazer testes (homologação)"), ("production", "Receber inscrições de verdade (produção)")))
    collector_id = forms.RegexField(label="ID da conta recebedora (opcional)", regex=r"^[1-9][0-9]{0,29}$", required=False, max_length=30,
        help_text="Pode deixar vazio: identificamos a conta automaticamente. Não informe o número da aplicação.")
    public_key = forms.CharField(label="Public Key", required=False, max_length=2048,
        widget=forms.PasswordInput(render_value=False), help_text="Da mesma aplicação e ambiente do Access Token. Vazio mantém a chave já salva.")
    access_token = forms.CharField(label="Access Token", required=False, max_length=2048,
        widget=forms.PasswordInput(render_value=False), help_text="Chave privada. Vazio mantém o token já salvo; o painel nunca o exibe.")
    webhook_secret = forms.CharField(label="Assinatura secreta do webhook", required=False, max_length=2048,
        widget=forms.PasswordInput(render_value=False), help_text="Obtida na configuração de Webhooks dessa aplicação. Vazio mantém o segredo já salvo.")
    confirm_connection = forms.BooleanField(required=False, label="Confirmo que estas chaves são da conta da Equipe Morcego e do ambiente escolhido, configurei o webhook e, para pagamentos reais, já concluí os testes em homologação.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].widget.attrs['id'] = 'mp-draft-version'
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs["class"] = "form-control"
        for name in ("access_token", "public_key", "webhook_secret"):
            self.fields[name].widget.attrs.update(autocomplete="new-password", spellcheck="false")
        self.fields["confirm_connection"].widget.attrs.pop("class", None)

    def clean(self):
        cleaned = super().clean()
        for name in ("access_token", "public_key", "webhook_secret"):
            value = cleaned.get(name, "")
            if value and any(ord(char) < 33 or ord(char) > 126 for char in value):
                self.add_error(name, "Cole somente a credencial, sem espaços ou quebras de linha.")
        return cleaned


class MercadoPagoOperationForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].widget.attrs['id'] = 'mp-' + self.__class__.__name__.lower() + '-version'


class MercadoPagoActivationForm(MercadoPagoOperationForm):
    confirm_account = forms.BooleanField(label="Conferi que o ID recebedor é da conta própria da Equipe Morcego.")
    confirm_environment = forms.BooleanField(label="As credenciais pertencem ao ambiente selecionado no Mercado Pago; selecionar sandbox aqui não transforma uma credencial de produção em teste.")
    confirm_webhook = forms.BooleanField(label="Configurei o webhook Pagamentos desta aplicação com a URL exibida e sua assinatura secreta.")
    confirm_sandbox = forms.BooleanField(required=False, label="Concluí a homologação em ambiente isolado, incluindo cobrança de teste, notificação assinada e conferência de valor/recebedor, antes de liberar produção.")
